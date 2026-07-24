#!/usr/bin/env python3
"""
pattern.py — 360°-stralingsdiagram meten vanaf het dashboard.

ONTWIKKELGEREEDSCHAP. Dit is geen operationele functie voor de verdelger
maar een meetinstrument voor onszelf; het mag weg zodra het zoekalgoritme
op echte getallen is afgesteld.

Waarom deze meting bestaat: het zoekalgoritme moet straks een SNR-piek over
een volle draai herkennen. Daarvoor hebben we drie getallen nodig die we nu
niet hebben — het werkelijke antennepatroon, het ruisniveau bij stilstand,
en de min/max SNR in onze opstelling. Zonder die getallen is elke drempel
in het algoritme een gok.

De sequentie per hoogte:

    stijg/klim naar hoogte -> settle -> 36 x (draai 10° -> settle ->
    5 packets meten) -> volgende hoogte -> land

Deze module draait BINNEN de service, net als mission.py: hij krijgt de
gedeelde 'status' dict en de bestaande MAVLink-connectie aangereikt via
get_mav(), en opent zelf niets. Dat is het hele verschil met de vorige
losse-script-versie, die de service moest stoppen om bij de USB-poort en
de SPI-bus te kunnen.

De LoRa-waarden komen uit dezelfde status-dict als het dashboard toont:
signal_power (RSSI), lora_snr (SNR) en lora_packet_count om te zien dat er
echt een nieuw packet binnen is. Eén bron, dus wat de CSV zegt is wat de
operator op het scherm zag.

VEILIGHEIDSMODEL (identiek aan mission.py)
  - De zender heeft ALTIJD voorrang. Zodra flight_mode niet meer GUIDED is,
    heeft de operator overgenomen: we stoppen onmiddellijk met commando's
    sturen, schrijven weg wat we hebben, en eindigen.
  - Er wordt NOOIT gedisarmd in de lucht. Landen gebeurt met LAND; de
    auto-disarm laten we aan ArduCopter over.
  - Bij een exception midden in een ronde wordt de tot dan verzamelde data
    alsnog weggeschreven.
"""

import csv
import os
import statistics
import threading
import time
from datetime import datetime

import mission


# ============================================
# MEETCONFIGURATIE
# ============================================

STAP_GRADEN        = 10
AANTAL_STAPPEN     = 36           # 36 x 10° = volledige draai
METINGEN_PER_HOEK  = 5
YAW_RATE_DPS       = 20

# De beacon zendt ~1 Hz. We wachten per meting op een NIEUW packet in plaats
# van blind te slapen; dat spreidt de metingen vanzelf ~1 s uit én maakt
# zichtbaar wanneer er niets binnenkomt (dat is zelf een meetresultaat).
PACKET_TIMEOUT_S   = 3.0

HEADING_TOLERANTIE = 3.0          # graden
HEADING_TIMEOUT_S  = 8.0
SETTLE_NA_DRAAI_S  = 1.0
SETTLE_NA_KLIM_S   = 3.0

HOOGTE_TOLERANTIE_M  = 0.3
HOOGTESTAP_TIMEOUT_S = 15.0
TAKEOFF_TIMEOUT_S    = 30.0

# Pre-flight eisen — gelijk aan mission.py
MIN_SATELLITES     = 8
REQUIRE_3D_FIX     = True

# Onder deze accustand slaan we de resterende rondes over. Eén ronde kost
# ~36 x (draai + 5 metingen) ≈ 4 min; met minder marge willen we landen.
MIN_BATTERIJ_VOLGENDE_RONDE = 40

# Grenzen voor de aangevraagde hoogtes. Spiegelt de geofence-marge uit
# mission.py: daarboven grijpt de fence in.
MIN_HOOGTE_M = 1.5
MAX_HOOGTE_M = 5.0

# Vaste meetomstandigheden, puur voor de CSV-header zodat een losse file
# later nog zelf-verklarend is.
BEACON_TX_DBM      = 2
LORA_CONFIG        = 'SF7 / BW125 kHz / 433.0 MHz'

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


# ============================================
# MEET-STATE (thread-safe gedeeld met dashboard)
# ============================================
# De frontend ontvangt deze bij elke hoekstap, zodat de operator tijdens de
# ~4 minuten per ronde ziet dat er iets gebeurt.

meting_state = {
    'active': False,
    'step': 'idle',
    'message': '',
    'aborted_by_pilot': False,
    'hoogte': None,                    # hoogte van de lopende ronde
    'stap': 0,                         # huidige hoekstap
    'totaal_stappen': AANTAL_STAPPEN,
}
_meting_lock = threading.Lock()
_meting_thread = None


def _set_state(step, message, active=True, **extra):
    """Update de gedeelde meet-state. Thread-safe."""
    with _meting_lock:
        meting_state['step'] = step
        meting_state['message'] = message
        meting_state['active'] = active
        meting_state.update(extra)
    print(f"[pattern] {step}: {message}")


def get_meting_state():
    """Lever een kopie van de meet-state (voor het dashboard)."""
    with _meting_lock:
        return dict(meting_state)


# ============================================
# HULPFUNCTIES
# ============================================

def _pilot_has_taken_over(status):
    """
    Kern van het veiligheidsmodel: elke andere mode dan GUIDED betekent dat
    de operator de zender heeft gebruikt of dat een failsafe getriggerd is.
    In beide gevallen stoppen we met sturen.
    """
    return status.get('flight_mode') != 'GUIDED'


def _wacht(seconden, status):
    """Wacht, maar breek af zodra de piloot overneemt. False = afgebroken."""
    einde = time.time() + seconden
    while time.time() < einde:
        if _pilot_has_taken_over(status):
            return False
        time.sleep(0.2)
    return True


def _hoekverschil(a, b):
    """Kleinste verschil tussen twee kompasrichtingen (0..180)."""
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d


def _clamp_hoogte(waarde):
    """Beperk een aangevraagde hoogte tot het toegestane bereik."""
    try:
        h = float(waarde)
    except (TypeError, ValueError):
        return None
    return max(MIN_HOOGTE_M, min(MAX_HOOGTE_M, h))


# ============================================
# MAVLINK-COMMANDO'S
# ============================================
# Zelfde fire-and-forget aanpak als mission.py: commando eruit, resultaat
# aflezen uit de status-dict die mavlink_loop toch al vult.

def _cmd_yaw_absoluut(get_mav, mavutil, graden):
    """
    Draai naar een ABSOLUTE kompasrichting (relative = 0).

    Bewust niet relatief (zoals mission._cmd_yaw wel doet): over 36 stappen
    zou de fout per stap zich opstapelen en zouden de laatste metingen bij
    een onbekende hoek horen. Absoluut houdt elke stap onafhankelijk.
    """
    mav = get_mav()
    if mav is None:
        return False
    mav.mav.command_long_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_CMD_CONDITION_YAW, 0,
        float(graden % 360),   # doelhoek in graden
        float(YAW_RATE_DPS),   # snelheid graden/sec
        1,                     # richting: 1 = met de klok mee
        0,                     # 0 = absoluut
        0, 0, 0)
    return True


def _cmd_hoogtestap(get_mav, mavutil, huidige_hoogte, doel_hoogte):
    """
    Klim of daal een RELATIEVE stap, op de huidige positie.

    Een tweede TAKEOFF werkt niet als de drone al vliegt, dus sturen we een
    positie-setpoint. Bewust in BODY_OFFSET_NED (net als mission._cmd_forward)
    en niet met absolute lat/lon/alt: bij een verkeerd frame zou GLOBAL_INT de
    hoogte als meters boven ZEENIVEAU lezen, en onze meetplek ligt op ~50 m
    MSL — de drone zou dan dalen tot hij de grond raakt. Relatief kan dat
    niet: een tekenfout kost hooguit een daling van de stapgrootte.

    z is negatief omdat NED naar beneden positief telt.
    """
    mav = get_mav()
    if mav is None:
        return False
    z = -(doel_hoogte - huidige_hoogte)

    # type_mask: alleen positie gebruiken, snelheid/versnelling negeren
    type_mask = 0b0000111111111000
    mav.mav.set_position_target_local_ned_send(
        0,
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_FRAME_BODY_OFFSET_NED,
        type_mask,
        0, 0, z,            # x=0, y=0, z = hoogteverschil (omlaag positief)
        0, 0, 0,
        0, 0, 0,
        0, 0)
    return True


# ============================================
# METEN
# ============================================

def _wacht_op_heading(status, doel):
    """
    Wacht tot de gemeten heading binnen de tolerantie van het doel ligt.

    Returns (bereikt, afgebroken_door_piloot). Een niet-bereikte heading is
    geen reden om te stoppen: we meten dan op de hoek waar hij staat en
    loggen die werkelijke hoek — beter een datapunt met de echte hoek dan
    een gat in het diagram.
    """
    einde = time.time() + HEADING_TIMEOUT_S
    while time.time() < einde:
        if _pilot_has_taken_over(status):
            return False, True
        if _hoekverschil(status.get('heading', 0.0), doel) <= HEADING_TOLERANTIE:
            return True, False
        time.sleep(0.1)
    return False, False


def _verzamel_metingen(status):
    """
    Verzamel METINGEN_PER_HOEK samples op de huidige hoek.

    We wachten per sample tot lora_packet_count oploopt in plaats van een
    vaste sleep: dat koppelt de meting aan de beacon in plaats van aan de
    klok, en een uitblijvend packet is meteen zichtbaar als n_metingen < 5.

    Returns (rssi_lijst, snr_lijst, afgebroken_door_piloot).
    """
    rssi_waarden, snr_waarden = [], []

    for _ in range(METINGEN_PER_HOEK):
        vorige_teller = status.get('lora_packet_count', 0)
        einde = time.time() + PACKET_TIMEOUT_S

        while time.time() < einde:
            if _pilot_has_taken_over(status):
                return rssi_waarden, snr_waarden, True
            if status.get('lora_packet_count', 0) > vorige_teller:
                rssi_waarden.append(status.get('signal_power', -120.0))
                snr_waarden.append(status.get('lora_snr', 0.0))
                break
            time.sleep(0.05)

    return rssi_waarden, snr_waarden, False


def _meetronde(status, get_mav, mavutil, emit_fn, hoogte, rijen):
    """
    Draai een volle cirkel en meet per stap. Vult 'rijen' onderweg, zodat de
    aanroeper ook bij een afbreking heeft wat er al gemeten is.

    Returns True als de ronde compleet is, False bij overname door de piloot.
    """
    start_heading = status.get('heading', 0.0)

    for stap in range(AANTAL_STAPPEN):
        doel = (start_heading + stap * STAP_GRADEN) % 360

        if _pilot_has_taken_over(status):
            return False

        _set_state('meten', f'Meting {hoogte} m — stap {stap + 1}/{AANTAL_STAPPEN}',
                   hoogte=hoogte, stap=stap + 1)
        emit_fn('meting_update', get_meting_state())

        _cmd_yaw_absoluut(get_mav, mavutil, doel)
        bereikt, afgebroken = _wacht_op_heading(status, doel)
        if afgebroken:
            return False
        if not bereikt:
            print(f"[pattern] heading {doel:.0f}° niet gehaald binnen "
                  f"{HEADING_TIMEOUT_S}s — meet op "
                  f"{status.get('heading', 0.0):.1f}°")

        if not _wacht(SETTLE_NA_DRAAI_S, status):
            return False

        gemeten_hoek = status.get('heading', 0.0)
        rssi_w, snr_w, afgebroken = _verzamel_metingen(status)

        rij = {
            'hoek_commando': round(doel, 1),
            'hoek_gemeten': round(gemeten_hoek, 1),
            'n_metingen': len(rssi_w),
            'tijdstip': datetime.now().strftime('%H:%M:%S'),
        }
        if rssi_w:
            # Mediaan en niet gemiddelde: in het veld zijn losse willekeurige
            # pieken gezien, en een mediaan laat zich daar niet door meeslepen.
            rij.update({
                'rssi_mediaan': round(statistics.median(rssi_w), 2),
                'rssi_min': round(min(rssi_w), 2),
                'rssi_max': round(max(rssi_w), 2),
                'snr_mediaan': round(statistics.median(snr_w), 2),
                'snr_min': round(min(snr_w), 2),
                'snr_max': round(max(snr_w), 2),
            })
        else:
            rij.update({
                'rssi_mediaan': '', 'rssi_min': '', 'rssi_max': '',
                'snr_mediaan': '', 'snr_min': '', 'snr_max': '',
            })

        rijen.append(rij)

        if afgebroken:
            return False

    return True


# ============================================
# UITVOER
# ============================================

def _schrijf_csv(hoogte, rijen, stempel, gps):
    """
    Schrijf één ronde weg. Losse file per hoogte, zodat een afgebroken
    tweede ronde de eerste niet aantast.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    pad = os.path.join(DATA_DIR, f'pattern_{stempel}_h{hoogte}m.csv')

    with open(pad, 'w', newline='') as f:
        f.write("# VespaTrack 360-graden stralingsdiagram\n")
        f.write(f"# meetmoment: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# hoogte: {hoogte} m\n")
        f.write(f"# meetplek: lat {gps[0]:.7f}, lon {gps[1]:.7f}\n")
        f.write(f"# metingen per hoek: {METINGEN_PER_HOEK}\n")
        f.write(f"# beacon zendvermogen: {BEACON_TX_DBM} dBm\n")
        f.write(f"# lora config: {LORA_CONFIG}\n")
        f.write("# hoek_gemeten is absolute MAVLink-heading, niet relatief\n")

        kolommen = ['hoek_commando', 'hoek_gemeten',
                    'rssi_mediaan', 'rssi_min', 'rssi_max',
                    'snr_mediaan', 'snr_min', 'snr_max',
                    'n_metingen', 'tijdstip']
        schrijver = csv.DictWriter(f, fieldnames=kolommen)
        schrijver.writeheader()
        for rij in rijen:
            schrijver.writerow(rij)

    print(f"[pattern] geschreven: {pad} ({len(rijen)} hoeken)")
    return pad


def _samenvatting(hoogte, rijen):
    """
    Log de getallen waar het ons om te doen was.

    De spreiding (max - min) per hoek is bij een stilhangende drone puur
    meetruis; het gemiddelde daarvan is dus onze ruisvloer. Het contrast
    tussen beste en slechtste hoek moet daar duidelijk bovenuit steken,
    anders is een SNR-piek niet betrouwbaar te herkennen.

    Returns een korte samenvattingsregel voor het dashboard.
    """
    met_data = [r for r in rijen if r['n_metingen'] > 0]
    zonder = [r for r in rijen if r['n_metingen'] == 0]

    print(f"[pattern] --- Samenvatting {hoogte} m ---")
    if not met_data:
        print("[pattern]   Geen enkel packet ontvangen op deze hoogte.")
        return f'{hoogte} m: geen packets ontvangen'

    beste = max(met_data, key=lambda r: r['snr_mediaan'])
    slechtste = min(met_data, key=lambda r: r['snr_mediaan'])
    contrast = beste['snr_mediaan'] - slechtste['snr_mediaan']
    spreiding = statistics.mean([r['snr_max'] - r['snr_min'] for r in met_data])

    print(f"[pattern]   Hoogste SNR : {beste['snr_mediaan']:+.2f} dB bij "
          f"{beste['hoek_commando']}° (gemeten {beste['hoek_gemeten']}°)")
    print(f"[pattern]   Laagste SNR : {slechtste['snr_mediaan']:+.2f} dB bij "
          f"{slechtste['hoek_commando']}° (gemeten {slechtste['hoek_gemeten']}°)")
    print(f"[pattern]   Contrast    : {contrast:.2f} dB")
    print(f"[pattern]   Gem. spreiding (ruisniveau): {spreiding:.2f} dB")
    print(f"[pattern]   Hoeken zonder packet       : {len(zonder)}/{len(rijen)}")

    if contrast <= spreiding:
        print("[pattern]   LET OP: contrast niet groter dan de ruis — een "
              "piek is op deze hoogte niet betrouwbaar te onderscheiden.")

    return (f'{hoogte} m: contrast {contrast:.1f} dB, '
            f'ruis {spreiding:.1f} dB, {len(zonder)} hoeken zonder packet')


# ============================================
# DE MEETSEQUENTIE
# ============================================

def _run_meting(status, get_mav, emit_fn, hoogtes):
    """
    De volledige meetsequentie. Draait in een aparte thread.

    Het veiligheidsmodel loopt door de HELE functie: na elke stap en tijdens
    elke wachttijd checken we of de piloot heeft overgenomen.
    """
    from pymavlink import mavutil

    stempel = datetime.now().strftime('%Y%m%d_%H%M')
    rondes = {}          # hoogte -> rijen, ook nodig in het finally-blok
    gps = (0.0, 0.0)

    def afbreken_door_piloot():
        with _meting_lock:
            meting_state['aborted_by_pilot'] = True
        _set_state('gestopt', 'Piloot heeft overgenomen — meting gestopt',
                   active=False)
        emit_fn('meting_update', get_meting_state())

    try:
        # ---- pre-flight ----
        _set_state('preflight', 'Pre-flight check...')
        emit_fn('meting_update', get_meting_state())

        if get_mav() is None:
            _set_state('fout', 'Pixhawk niet verbonden', active=False)
            emit_fn('meting_update', get_meting_state())
            return

        sats = status.get('gps_satellites', 0)
        if REQUIRE_3D_FIX and not status.get('gps_fix', False):
            _set_state('fout', 'Geen 3D GPS-fix — meting afgebroken', active=False)
            emit_fn('meting_update', get_meting_state())
            return
        if sats < MIN_SATELLITES:
            _set_state('fout',
                       f'Te weinig satellieten ({sats}/{MIN_SATELLITES}) — '
                       f'meting afgebroken', active=False)
            emit_fn('meting_update', get_meting_state())
            return

        gps = (status.get('gps_lat', 0.0), status.get('gps_lon', 0.0))

        # ---- opstijgen naar de eerste hoogte ----
        eerste = hoogtes[0]
        _set_state('guided', 'GUIDED-mode instellen...')
        emit_fn('meting_update', get_meting_state())
        mission._cmd_set_mode_guided(get_mav, mavutil)
        if not _wacht(2, status):
            afbreken_door_piloot(); return

        _set_state('arm', 'Armen...')
        emit_fn('meting_update', get_meting_state())
        mission._cmd_arm(get_mav, mavutil)
        if not _wacht(3, status):
            afbreken_door_piloot(); return

        if not status.get('armed', False):
            _set_state('fout', 'Armen mislukt (check RC aan + GPS) — '
                               'meting afgebroken', active=False)
            emit_fn('meting_update', get_meting_state())
            return

        _set_state('takeoff', f'Opstijgen naar {eerste} m...')
        emit_fn('meting_update', get_meting_state())
        mission._cmd_takeoff(get_mav, mavutil, eerste)

        deadline = time.time() + TAKEOFF_TIMEOUT_S
        while time.time() < deadline:
            if _pilot_has_taken_over(status):
                afbreken_door_piloot(); return
            if status.get('altitude', 0) >= eerste * 0.95:
                break
            time.sleep(0.3)

        if not _wacht(SETTLE_NA_KLIM_S, status):
            afbreken_door_piloot(); return

        # ---- rondes ----
        for index, hoogte in enumerate(hoogtes):
            if index > 0:
                # Batterijcheck vóór elke volgende ronde: liever landen met
                # één bruikbare ronde dan halverwege de tweede stranden.
                accu = status.get('battery_percent', -1)
                if 0 <= accu < MIN_BATTERIJ_VOLGENDE_RONDE:
                    _set_state('batterij',
                               f'Batterij {accu}% — resterende rondes '
                               f'overgeslagen, landen')
                    emit_fn('meting_update', get_meting_state())
                    break

                _set_state('klim', f'Klimmen naar {hoogte} m...', hoogte=hoogte)
                emit_fn('meting_update', get_meting_state())
                _cmd_hoogtestap(get_mav, mavutil,
                                status.get('altitude', 0.0), hoogte)

                # Tolerantie op absolute afstand tot het doel: bij een
                # relatieve stap zegt "95% van de doelhoogte" niets over of
                # de stap zelf gelukt is.
                deadline = time.time() + HOOGTESTAP_TIMEOUT_S
                while time.time() < deadline:
                    if _pilot_has_taken_over(status):
                        afbreken_door_piloot(); return
                    if abs(status.get('altitude', 0.0) - hoogte) <= HOOGTE_TOLERANTIE_M:
                        break
                    time.sleep(0.3)

                if not _wacht(SETTLE_NA_KLIM_S, status):
                    afbreken_door_piloot(); return

            rijen = []
            rondes[hoogte] = rijen
            if not _meetronde(status, get_mav, mavutil, emit_fn, hoogte, rijen):
                afbreken_door_piloot(); return

        # ---- landen ----
        _set_state('landen', 'Meting klaar — landen...')
        emit_fn('meting_update', get_meting_state())
        mission._cmd_land(get_mav, mavutil)

    except Exception as e:
        _set_state('fout', f'Fout tijdens meting: {e}', active=False)
        emit_fn('meting_update', get_meting_state())
        print(f"[pattern] FOUT: {e}")

    finally:
        # Wat er ook gebeurd is: wegschrijven en samenvatten wat we hebben.
        samenvattingen = []
        for hoogte, rijen in rondes.items():
            if rijen:
                _schrijf_csv(hoogte, rijen, stempel, gps)
                samenvattingen.append(_samenvatting(hoogte, rijen))

        with _meting_lock:
            afgebroken = meting_state['aborted_by_pilot']
            fout = meting_state['step'] == 'fout'

        if not afgebroken and not fout:
            tekst = ' | '.join(samenvattingen) if samenvattingen \
                else 'geen data verzameld'
            _set_state('klaar', f'Meting voltooid — {tekst}', active=False)
        else:
            # Bij afbreken/fout staat de melding er al; alleen active uit.
            with _meting_lock:
                meting_state['active'] = False

        emit_fn('meting_update', get_meting_state())


# ============================================
# PUBLIEKE API (aangeroepen vanuit app.py)
# ============================================

def start_meting(status, get_mav, emit_fn, hoogtes=None):
    """
    Start de stralingsdiagram-meting in een aparte thread.

    Weigert te starten als er al een meting loopt OF als mission.py een
    missie draait: twee threads die tegelijk MAVLink-commando's naar
    dezelfde drone sturen is niet iets wat je in de lucht wilt uitzoeken.

    Returns (success: bool, message: str) voor directe feedback.
    """
    global _meting_thread

    with _meting_lock:
        if meting_state['active']:
            return False, 'Er loopt al een meting'

    if mission.get_mission_state().get('active'):
        return False, 'Er loopt een missie — wacht tot die klaar is'

    # Hoogtes opschonen: clampen op het toegestane bereik en onbruikbare
    # waarden eruit. Zonder geldige hoogte heeft starten geen zin.
    gevraagd = hoogtes if isinstance(hoogtes, (list, tuple)) else [2.0, 4.0]
    schoon = [h for h in (_clamp_hoogte(x) for x in gevraagd) if h is not None]
    if not schoon:
        return False, 'Geen geldige hoogtes opgegeven'

    with _meting_lock:
        meting_state['aborted_by_pilot'] = False
        meting_state['hoogte'] = None
        meting_state['stap'] = 0

    _meting_thread = threading.Thread(
        target=_run_meting,
        args=(status, get_mav, emit_fn, schoon),
        daemon=True,
        name='stralingsdiagram'
    )
    _meting_thread.start()

    hoogte_tekst = ' en '.join(f'{h} m' for h in schoon)
    return True, f'Meting gestart ({hoogte_tekst})'
