#!/usr/bin/env python3
"""
mission.py — Autonome demo-missie voor VespaTrack

Een voorgeprogrammeerde, sensor-onafhankelijke vluchtsequentie die het
autonome vliegen zelf bewijst voordat er op signalen gereageerd wordt.

De missie kan vanuit twee situaties starten en detecteert zelf welke;
één knop, geen keuzemenu:

    vanaf de grond : GUIDED -> ARM -> stijg naar zoekhoogte -+
    vanuit de lucht: GUIDED (alleen overnemen) --------------+
                                                             |
        +----------------------------------------------------+
        -> hover -> 360° draai -> hover -> 1.5m vooruit
        -> hover -> land (auto-disarm)

"Vanuit de lucht" betekent dat de operator handmatig is opgestegen en
positie gekozen heeft; de drone zoekt dan op de hoogte waar hij hangt.
Het onderscheid gaat via armed + altitude > MIN_AIRBORNE_ALT_M.

De zoekhoogte is instelbaar vanuit het dashboard (1,5–5 m, default 2,5 m)
en wordt server-side geclampt tegen de geofence. Hij geldt alleen voor de
start vanaf de grond — hangt de drone al, dan is hij niet van toepassing.

De pre-flight GPS-check geldt in BEIDE gevallen: hij kost niets en vangt
het geval waarin de fix wegvalt tussen handmatig opstijgen en het
drukken op START.

VEILIGHEIDSMODEL (belangrijk!):
  - De zender heeft ALTIJD voorrang. Zet de operator de SwC-switch naar
    LOITER / STABILIZE / RTL, dan verlaat de drone GUIDED. Deze module
    detecteert dat (mode != GUIDED) en STOPT onmiddellijk met het sturen
    van commando's. Het script vecht nooit tegen de operator.
  - Elke landing (missie-einde, RTL, LAND) leidt tot auto-disarm door
    ArduCopter zelf. Deze module disarmt NOOIT in de lucht.
  - LOITER (STOP & HANG) laat de drone hangen; disarmen doet de operator
    zelf op de grond.

Deze module praat NIET rechtstreeks met de seriële poort. Hij krijgt de
bestaande, gedeelde MAVLink-connectie aangereikt via een getter-functie,
zodat er geen tweede verbinding ontstaat die botst met mavlink_loop().

Draait in een aparte thread (gestart vanuit app.py). De thread leest de
'status' dict om te weten wat de drone doet (hoogte, mode) — dat is de
fire-and-forget aanpak: commando's sturen, voortgang aflezen uit de
telemetrie die mavlink_loop() toch al binnenhaalt.
"""

import time
import threading


# ============================================
# MISSIE-PARAMETERS (rustige, veilige eerste vlucht)
# ============================================
# Deze matchen de Pixhawk-parameters die we rustig hebben gezet
# (WPNAV_SPEED_UP=50, WPNAV_SPEED=150, etc). De hoogte/afstand hier
# bepalen de vorm van de missie; de snelheden zitten in de Pixhawk.

# ============================================
# HOOGTEGRENZEN
# ============================================
# De harde bovengrens is de geofence op de Pixhawk (FENCE_ALT_MAX = 10 m,
# FENCE_ENABLE = 1, FENCE_TYPE = 1, FENCE_ACTION = 3 -> SmartRTL).
# Daar houden we bewust ruime marge onder: in het veld schommelde de drone
# ~1 m boven het setpoint, en bij wind kan dat meer zijn. 5 m zoekhoogte
# laat dus minstens 4 m over voordat de fence ingrijpt.
#
# 5 m valt samen met RTL_ALT (500 cm). Zit de drone daarboven door
# overshoot, dan vliegt een RTL horizontaal terug op de huidige hoogte
# in plaats van eerst te klimmen. Op een open demoveld is dat veilig.
#
# Deze waarden moeten matchen met MISSION_MIN/MAX/DEFAULT_ALT_M in
# static/js/mission-controls.js.
FENCE_ALT_MAX_M       = 10.0   # spiegelt de Pixhawk-parameter
ALT_SAFETY_MARGIN_M   = 5.0    # marge voor overshoot en wind
MIN_TAKEOFF_ALT_M     = 1.5
MAX_TAKEOFF_ALT_M     = FENCE_ALT_MAX_M - ALT_SAFETY_MARGIN_M   # = 5.0
DEFAULT_TAKEOFF_ALT_M = 2.5

FORWARD_DIST_M  = 2.0    # vooruit-afstand in meters
STEP_PAUSE_S    = 3.0    # pauze tussen stappen (rustig, volgbaar)
YAW_DEGREES     = 360    # volledige draai
YAW_RATE_DPS    = 20     # draaisnelheid in graden/seconde

# Pre-flight eisen — hieronder start de missie niet
MIN_SATELLITES  = 8      # minimaal aantal GPS-satellieten
REQUIRE_3D_FIX  = True   # 3D-fix vereist

# Drempel om "in de lucht" te onderscheiden van GPS-hoogteruis op de
# grond. De drone rapporteerde op de grond waarden tot ~0,6 m.
#
# Gelijk aan MIN_TAKEOFF_ALT_M: onder die hoogte hangt de drone nooit
# bewust, dus alles daaronder is grond. Op 1,0 m bestond nog een grijze
# zone waarin de schommelende hoogtemeting de grondtak kon kiezen terwijl
# de drone al hing — die zou dan een takeoff commanderen en een
# onverwachte klim geven. Met 1,5 m is die zone weg.
MIN_AIRBORNE_ALT_M = 1.5

# Hoe lang wachten tot de drone zijn doelhoogte bereikt heeft (veiligheid:
# nooit oneindig wachten als er iets misgaat).
#
# Gekalibreerd op de maximale zoekhoogte van 5 m bij WPNAV_SPEED_UP = 50
# (50 cm/s): ~10 s klimmen plus armtijd en marge. Verandert WPNAV_SPEED_UP,
# dan moet deze waarde mee.
TAKEOFF_TIMEOUT_S = 25


# ============================================
# MISSIE-STATE (thread-safe gedeeld met dashboard)
# ============================================
# De frontend pollt/ontvangt deze zodat de operator ziet wat de missie doet.

mission_state = {
    'active': False,        # loopt er een missie?
    'step': 'idle',         # huidige stap (mensleesbaar)
    'message': '',          # detail voor het dashboard
    'aborted_by_pilot': False,  # True als operator via zender overnam
}
_mission_lock = threading.Lock()
_mission_thread = None


def _set_state(step, message, active=True):
    """Update de gedeelde missie-state. Thread-safe."""
    with _mission_lock:
        mission_state['step'] = step
        mission_state['message'] = message
        mission_state['active'] = active
    print(f"[mission] {step}: {message}")


def get_mission_state():
    """Lever een kopie van de missie-state (voor het dashboard)."""
    with _mission_lock:
        return dict(mission_state)


# ============================================
# HULPFUNCTIES
# ============================================

def _clamp_altitude(value):
    """
    Beperk de aangeleverde zoekhoogte tot het toegestane bereik.

    Herhaalt bewust de clamp die de browser al doet: een client kan
    altijd iets anders sturen dan het invoerveld toestaat. Dit is de
    grens die telt.

    Bij None, een lege waarde of onparsebare invoer valt hij terug op
    DEFAULT_TAKEOFF_ALT_M in plaats van te falen — een missie die op
    2,5 m start is beter dan een crash in de handler.
    """
    try:
        alt = float(value)
    except (TypeError, ValueError):
        return DEFAULT_TAKEOFF_ALT_M
    return max(MIN_TAKEOFF_ALT_M, min(MAX_TAKEOFF_ALT_M, alt))


def _pilot_has_taken_over(status):
    """
    Kern van het veiligheidsmodel: als de vluchtmode niet (meer) GUIDED is,
    heeft de operator via de zender-switch overgenomen (LOITER/RTL/STABILIZE)
    of is er een failsafe getriggerd. In beide gevallen moeten we STOPPEN.

    Returns True als we de controle NIET meer hebben.
    """
    return status.get('flight_mode') != 'GUIDED'


def _wait_with_pilot_check(seconds, status, get_mav):
    """
    Wacht 'seconds' seconden, maar controleer elke 0.2s of de piloot heeft
    overgenomen. Zodra dat zo is, returnen we False (missie moet stoppen).

    Returns True als de wachttijd normaal verstreek, False bij overname.
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        if _pilot_has_taken_over(status):
            return False
        time.sleep(0.2)
    return True


# ============================================
# MAVLINK-COMMANDO'S
# ============================================
# Elk commando gebruikt de gedeelde connectie via get_mav(). We sturen
# fire-and-forget: het commando gaat eruit, en we lezen het resultaat af
# uit de 'status' dict (die mavlink_loop vult) in plaats van hier op een
# ack te wachten — dat voorkomt de ack-race met mavlink_loop.

def _cmd_set_mode_guided(get_mav, mavutil):
    """Zet de drone in GUIDED-mode."""
    mav = get_mav()
    if mav is None:
        return False
    mav.set_mode('GUIDED')
    return True


def _cmd_arm(get_mav, mavutil):
    """Arm de motoren."""
    mav = get_mav()
    if mav is None:
        return False
    mav.mav.command_long_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
        1, 0, 0, 0, 0, 0, 0)
    return True


def _cmd_takeoff(get_mav, mavutil, altitude_m):
    """
    Stuur een GUIDED-takeoff naar de gegeven hoogte. De Pixhawk regelt de
    klim zelf (met WPNAV_SPEED_UP als snelheid) — geen throttle-stick nodig.
    """
    mav = get_mav()
    if mav is None:
        return False
    mav.mav.command_long_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_CMD_NAV_TAKEOFF, 0,
        0, 0, 0, 0, 0, 0, altitude_m)
    return True


def _cmd_yaw(get_mav, mavutil, degrees, rate_dps):
    """
    Draai de drone om zijn as. Relatief (relative=1) t.o.v. huidige heading,
    met klok mee (direction=1).
    """
    mav = get_mav()
    if mav is None:
        return False
    mav.mav.command_long_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_CMD_CONDITION_YAW, 0,
        degrees,    # doelhoek
        rate_dps,   # snelheid graden/sec
        1,          # richting: 1 = met klok mee
        1,          # 1 = relatief aan huidige heading
        0, 0, 0)
    return True


def _cmd_forward(get_mav, mavutil, distance_m):
    """
    Vlieg 'distance_m' meter vooruit in BODY-frame (relatief aan de neus
    van de drone). Gebruikt SET_POSITION_TARGET_LOCAL_NED met de
    BODY_OFFSET frame-vlag. x = vooruit, y = rechts, z = omlaag (NED).
    """
    mav = get_mav()
    if mav is None:
        return False
    # type_mask: alleen positie gebruiken, snelheid/versnelling negeren
    type_mask = 0b0000111111111000
    mav.mav.set_position_target_local_ned_send(
        0,
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_FRAME_BODY_OFFSET_NED,
        type_mask,
        distance_m, 0, 0,   # x=vooruit, y=0, z=0
        0, 0, 0,
        0, 0, 0,
        0, 0)
    return True


def _cmd_land(get_mav, mavutil):
    """
    Zet de drone in LAND-mode. Hij daalt op de huidige positie en
    ArduCopter disarmt automatisch zodra hij de grond raakt.
    """
    mav = get_mav()
    if mav is None:
        return False
    mav.set_mode('LAND')
    return True


# ============================================
# DE MISSIE-SEQUENTIE
# ============================================

def _run_mission(status, get_mav, emit_fn, takeoff_alt_m):
    """
    De volledige demo-sequentie. Draait in een aparte thread.

    Argumenten:
      status  : de gedeelde status-dict uit app.py (live telemetrie)
      get_mav : functie die de huidige MAVLink-connectie teruggeeft (of None)
      emit_fn : functie om een bericht naar het dashboard te sturen
                (bijv. socketio.emit) — signatuur emit_fn(event, data)
      takeoff_alt_m : zoekhoogte in meters, al geclampt door start_mission()

    Het veiligheidsmodel loopt door de HELE functie: na elke stap en tijdens
    elke wachttijd checken we of de piloot heeft overgenomen. Zo ja: stoppen.
    """
    from pymavlink import mavutil

    def abort_if_pilot():
        """Helper: check overname, zet state en return True als we moeten stoppen."""
        if _pilot_has_taken_over(status):
            with _mission_lock:
                mission_state['aborted_by_pilot'] = True
            _set_state('gestopt', 'Piloot heeft overgenomen via zender — missie gestopt', active=False)
            emit_fn('mission_update', get_mission_state())
            return True
        return False

    # Reset de abort-vlag bij een nieuwe start
    with _mission_lock:
        mission_state['aborted_by_pilot'] = False

    # ---- PRE-FLIGHT CHECK ----
    _set_state('preflight', 'Pre-flight check...')
    emit_fn('mission_update', get_mission_state())

    if get_mav() is None:
        _set_state('fout', 'Pixhawk niet verbonden', active=False)
        emit_fn('mission_update', get_mission_state())
        return

    # GPS-kwaliteit controleren uit de live status
    sats = status.get('gps_satellites', 0)
    has_fix = status.get('gps_fix', False)
    if REQUIRE_3D_FIX and not has_fix:
        _set_state('fout', f'Geen 3D GPS-fix — missie afgebroken', active=False)
        emit_fn('mission_update', get_mission_state())
        return
    if sats < MIN_SATELLITES:
        _set_state('fout', f'Te weinig satellieten ({sats}/{MIN_SATELLITES}) — missie afgebroken', active=False)
        emit_fn('mission_update', get_mission_state())
        return

    # Hangt de drone al? Dan heeft de operator handmatig opgestegen en
    # positie gekozen; wij nemen alleen over en zoeken op de hoogte waar
    # hij staat. Armen en opstijgen zou in dat geval betekenisloos of
    # gevaarlijk zijn (een TAKEOFF-commando in de lucht laat de drone
    # opnieuw klimmen naar de doelhoogte).
    al_in_de_lucht = (status.get('armed', False)
                      and status.get('altitude', 0) > MIN_AIRBORNE_ALT_M)

    if al_in_de_lucht:
        # ---- OVERNEMEN IN DE LUCHT ----
        hoogte = round(status.get('altitude', 0), 1)
        _set_state('guided', f'Overnemen op {hoogte} m...')
        emit_fn('mission_update', get_mission_state())
        _cmd_set_mode_guided(get_mav, mavutil)
        if not _wait_with_pilot_check(2, status, get_mav):
            abort_if_pilot(); return

        # Laten uitzweven na de mode-switch: LOITER en GUIDED gebruiken
        # verschillende controllers, dus de overgang geeft een kleine
        # positiecorrectie. De grondtak heeft deze pauze al na de takeoff.
        # Zonder hem komt de eerste meting van de 360°-draai van een nog
        # niet stilhangende drone — precies het datapunt waarmee de rest
        # van de draai vergeleken wordt.
        if not _wait_with_pilot_check(STEP_PAUSE_S, status, get_mav):
            abort_if_pilot(); return
    else:
        # ---- GUIDED ----
        _set_state('guided', 'GUIDED-mode instellen...')
        emit_fn('mission_update', get_mission_state())
        _cmd_set_mode_guided(get_mav, mavutil)
        if not _wait_with_pilot_check(2, status, get_mav):
            abort_if_pilot(); return

        # ---- ARM ----
        _set_state('arm', 'Armen...')
        emit_fn('mission_update', get_mission_state())
        _cmd_arm(get_mav, mavutil)
        if not _wait_with_pilot_check(3, status, get_mav):
            abort_if_pilot(); return

        # Controleer of we echt gearmd zijn
        if not status.get('armed', False):
            _set_state('fout', 'Armen mislukt (check RC aan + GPS) — missie afgebroken', active=False)
            emit_fn('mission_update', get_mission_state())
            return

        # ---- TAKEOFF ----
        _set_state('takeoff', f'Opstijgen naar {takeoff_alt_m} m...')
        emit_fn('mission_update', get_mission_state())
        _cmd_takeoff(get_mav, mavutil, takeoff_alt_m)

        # Wacht tot de doelhoogte bereikt is (of timeout), met overname-check
        deadline = time.time() + TAKEOFF_TIMEOUT_S
        while time.time() < deadline:
            if abort_if_pilot():
                return
            alt = status.get('altitude', 0)
            if alt >= takeoff_alt_m * 0.95:   # 95% = hoog genoeg
                break
            time.sleep(0.3)

        if not _wait_with_pilot_check(STEP_PAUSE_S, status, get_mav):
            abort_if_pilot(); return

    # ---- 360° DRAAI ----
    _set_state('yaw', 'Draaien 360°...')
    emit_fn('mission_update', get_mission_state())
    _cmd_yaw(get_mav, mavutil, YAW_DEGREES, YAW_RATE_DPS)

    # Duur van de draai = graden / snelheid, plus wat marge
    draai_duur = (YAW_DEGREES / YAW_RATE_DPS) + 2
    if not _wait_with_pilot_check(draai_duur, status, get_mav):
        abort_if_pilot(); return
    if not _wait_with_pilot_check(STEP_PAUSE_S, status, get_mav):
        abort_if_pilot(); return

    # ---- VOORUIT ----
    _set_state('forward', f'{FORWARD_DIST_M} m vooruit...')
    emit_fn('mission_update', get_mission_state())
    _cmd_forward(get_mav, mavutil, FORWARD_DIST_M)
    if not _wait_with_pilot_check(7, status, get_mav):   # tijd om te bewegen
        abort_if_pilot(); return
    if not _wait_with_pilot_check(STEP_PAUSE_S, status, get_mav):
        abort_if_pilot(); return

    # ---- LANDEN ----
    _set_state('land', 'Landen (auto-disarm na landing)...')
    emit_fn('mission_update', get_mission_state())
    _cmd_land(get_mav, mavutil)

    # We wachten tot de drone gedisarmd is (auto-disarm door ArduCopter).
    # Hier checken we NIET meer op piloot-overname: als de operator tijdens
    # de landing naar RTL of LOITER gaat, is dat prima — LAND was toch al
    # het einde. We wachten gewoon tot armed False wordt.
    land_deadline = time.time() + 60
    while time.time() < land_deadline:
        if not status.get('armed', True):
            break
        time.sleep(0.5)

    _set_state('klaar', 'Missie voltooid — drone geland en gedisarmd', active=False)
    emit_fn('mission_update', get_mission_state())


# ============================================
# PUBLIEKE API (aangeroepen vanuit app.py)
# ============================================

def start_mission(status, get_mav, emit_fn, takeoff_alt_m=None):
    """
    Start de zoekmissie in een aparte thread. Doet niets als er al een
    missie loopt (voorkomt dubbele starts).

    takeoff_alt_m komt uit het dashboard en wordt hier geclampt vóór de
    thread start — zo krijgt _run_mission altijd een geldige waarde en
    hoeft die functie zelf niet te valideren.

    Returns (success: bool, message: str) voor directe feedback.
    """
    global _mission_thread

    with _mission_lock:
        if mission_state['active']:
            return False, 'Er loopt al een missie'

    alt = _clamp_altitude(takeoff_alt_m)

    _mission_thread = threading.Thread(
        target=_run_mission,
        args=(status, get_mav, emit_fn, alt),
        daemon=True,
        name='zoek-missie'
    )
    _mission_thread.start()
    return True, f'Missie gestart (zoekhoogte {alt} m)'