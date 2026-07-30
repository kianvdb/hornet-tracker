#!/usr/bin/env python3
"""
approach.py — vooruit-nadering van de beacon (afstand-naar-signaal curve).

ONTWIKKELGEREEDSCHAP. Net als pattern.py geen operationele functie maar een
meetinstrument; het mag weg zodra het zoekalgoritme op echte getallen staat.

Waarom deze meting bestaat: de rotatiemetingen (pattern.py) zijn afgerond en
lieten zien dat de beacon een echte, hoogte-onafhankelijke bron is met een
brede peiling (~±30°). Wat nog ontbrak is het GEDRAG BIJ BEWEGING: hoe
verandert de SNR als de drone recht op de beacon af vliegt, en — cruciaal —
waar precies ligt de sterkste-signaal-positie ten opzichte van de beacon zelf?

Door de hoogtegeometrie (directe straal + grondreflectie) kan de SNR-piek
vóór de beacon liggen in plaats van erop. Als dat zo is en we sturen de
verdelger naar de gelogde piek, staat hij bij de verkeerde boom. Deze meting
meet die systematische offset: de drone vliegt van 15 m afstand recht op de
beacon af en eroverheen, en logt SNR/RSSI/GPS per stap. De operator legt de
piek-positie naast de met meetlint gemarkeerde beacon-positie.

De neus blijft naar de beacon wijzen — de operator richt de drone bij het
opstijgen, en we draaien NIET tijdens de meting. Elke stap is een
BODY_OFFSET_NED vooruit-zet (mission._cmd_forward).

VEILIGHEIDSMODEL (identiek aan pattern.py / mission.py)
  - De zender heeft ALTIJD voorrang. Zodra flight_mode niet meer GUIDED is,
    stoppen we onmiddellijk met commando's sturen, schrijven weg wat we
    hebben, en eindigen.
  - Nooit disarmen in de lucht. Terug naar huis gebeurt met RTL.
  - Bij een exception wordt de tot dan verzamelde data alsnog weggeschreven.
"""

import csv
import math
import os
import statistics
import threading
import time
from datetime import datetime

import mission
import pattern


# ============================================
# MEETCONFIGURATIE
# ============================================

# Afgelegde afstanden vanaf de startpositie, in meters. Grof waar het signaal
# vlak is (ver van de beacon), fijn waar de piek verwacht wordt (rond de
# beacon op 15 m). Pas deze lijst aan om de meting anders te verdelen; de
# vooruit-stappen worden als opeenvolgende verschillen berekend.
#
# 0..9 m in grove stappen (0, 5, 9), 9..20 m in stappen van 1 m. De beacon
# staat op 15 m, dus de fijne zone loopt van 6 m vóór tot 5 m voorbij de
# beacon — dat vangt de piek en de daling erna.
POSITIES_M = [0, 5, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]  # 14 metingen

BEACON_AFSTAND_M = 15   # fysiek met meetlint gemarkeerd, puur voor de CSV-header

# Aantal SNR/RSSI-samples per positie. We hergebruiken pattern._verzamel_metingen,
# dat pattern.METINGEN_PER_HOEK gebruikt — dus die is de bron, en we spiegelen
# hem hier zodat de CSV-header niet kan gaan liegen als pattern verandert.
METINGEN_PER_STAP = pattern.METINGEN_PER_HOEK
PACKET_TIMEOUT_S = pattern.PACKET_TIMEOUT_S   # ~1 Hz beacon; wacht op nieuw packet

SETTLE_NA_STAP_S = 2.0
SETTLE_NA_KLIM_S = 3.0
TAKEOFF_TIMEOUT_S = 30.0

# Vooruit-stap: detectie dat de beweging klaar is. We commanderen relatief
# (BODY_OFFSET_NED) en kunnen dus geen doel-lat/lon uitlezen; in plaats
# daarvan wachten we tot de drone daadwerkelijk ~de stapafstand heeft
# afgelegd EN de positie weer stil ligt. Robuuster dan blind slapen op een
# aangenomen snelheid.
STAP_RAMING_SNELHEID = 1.0   # m/s, conservatief (WPNAV_SPEED ~1,5 m/s minus accel/decel)
STAP_BASIS_TIMEOUT_S = 4.0
STAP_STABIEL_M = 0.4         # positie geldt als stil binnen deze straal...
STAP_STABIEL_DUUR_S = 1.5    # ...gedurende deze tijd

# Pre-flight eisen — gelijk aan pattern.py / mission.py
MIN_SATELLITES = 8
REQUIRE_3D_FIX = True
BEACON_MAX_AGE_S = pattern.BEACON_MAX_AGE_S

# Hoogtekeuze in de modal: 2 / 3 / 4 m, standaard 3. Voorlopig vliegt de
# operator alleen 3 m; de keuze staat er voor een latere tweede hoogte.
TOEGESTANE_HOOGTES = (2.0, 3.0, 4.0)
DEFAULT_HOOGTE_M = 3.0

BEACON_TX_DBM = 2
LORA_CONFIG = 'SF7 / BW125 kHz / 433.2 MHz'

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


# ============================================
# MEET-STATE (thread-safe gedeeld met dashboard)
# ============================================
# Eigen state, los van pattern.py. De start-guard controleert wel of er al
# een pattern-meting of een missie loopt — twee threads die tegelijk naar
# dezelfde drone sturen wil je niet in de lucht uitzoeken.

approach_state = {
    'active': False,
    'step': 'idle',
    'message': '',
    'aborted_by_pilot': False,
    'hoogte': None,
    'stap': 0,
    'totaal_stappen': len(POSITIES_M),
}
_lock = threading.Lock()
_thread = None


def _set_state(step, message, active=True, **extra):
    """Update de gedeelde meet-state. Thread-safe."""
    with _lock:
        approach_state['step'] = step
        approach_state['message'] = message
        approach_state['active'] = active
        approach_state.update(extra)
    print(f"[approach] {step}: {message}")


def get_approach_state():
    """Lever een kopie van de meet-state (voor het dashboard)."""
    with _lock:
        return dict(approach_state)


# ============================================
# HULPFUNCTIES
# ============================================

def _clamp_hoogte(waarde):
    """
    Kies de dichtstbijzijnde toegestane hoogte (2/3/4 m).

    Bij None of onparsebaar valt hij terug op de default; een onmogelijke
    hoogte versturen is erger dan de standaardkeuze nemen.
    """
    try:
        h = float(waarde)
    except (TypeError, ValueError):
        return DEFAULT_HOOGTE_M
    return min(TOEGESTANE_HOOGTES, key=lambda t: abs(t - h))


def _horizontale_afstand(lat1, lon1, lat2, lon2):
    """Platte-aarde afstand in meters (ruim genoeg voor tientallen meters)."""
    dn = (lat1 - lat2) * 111320.0
    de = (lon1 - lon2) * 111320.0 * math.cos(math.radians(lat1))
    return math.hypot(dn, de)


def _cmd_rtl(get_mav):
    """Terug naar het opstijgpunt en landen. Geen eigen disarm — RTL landt
    en ArduCopter disarmt zelf."""
    mav = get_mav()
    if mav is None:
        return False
    mav.set_mode('RTL')
    return True


def _wacht_op_stap(status, start_lat, start_lon, delta_m):
    """
    Wacht tot de vooruit-stap voltooid is: de drone heeft ~delta_m afgelegd
    EN de positie ligt weer stil.

    Returns (voltooid, afgebroken_door_piloot). Een niet-gehaalde stap is
    geen reden om te stoppen — we meten dan op de plek waar hij staat en de
    gelogde GPS-positie laat zien wat er werkelijk gebeurd is.
    """
    timeout = STAP_BASIS_TIMEOUT_S + delta_m / STAP_RAMING_SNELHEID + 3.0
    deadline = time.time() + timeout
    recent = []   # (t, lat, lon) voor stabiliteitscheck

    while time.time() < deadline:
        if pattern._pilot_has_taken_over(status):
            return False, True

        lat = status.get('gps_lat', 0.0)
        lon = status.get('gps_lon', 0.0)
        nu = time.time()
        recent.append((nu, lat, lon))
        recent = [r for r in recent if nu - r[0] <= STAP_STABIEL_DUUR_S]

        afgelegd = _horizontale_afstand(lat, lon, start_lat, start_lon)
        # Genoeg bewogen (>60% van de stap) en positie stil over het venster?
        if afgelegd >= 0.6 * delta_m and len(recent) >= 3:
            spreiding = max(
                _horizontale_afstand(a[1], a[2], b[1], b[2])
                for a in recent for b in recent)
            if spreiding <= STAP_STABIEL_M:
                return True, False

        time.sleep(0.3)

    return False, False


# ============================================
# UITVOER
# ============================================

def _schrijf_csv(hoogte, rijen, stempel, start_gps):
    """Schrijf de nadering weg. Eén bestand per vlucht."""
    os.makedirs(DATA_DIR, exist_ok=True)
    pad = os.path.join(DATA_DIR, f'approach_{stempel}_h{hoogte}m.csv')

    with open(pad, 'w', newline='') as f:
        f.write("# VespaTrack vooruit-nadering van de beacon\n")
        f.write(f"# meetmoment: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# hoogte: {hoogte} m\n")
        f.write(f"# GPS-startpositie: lat {start_gps[0]:.7f}, lon {start_gps[1]:.7f}\n")
        f.write(f"# beacon-afstand vanaf start: {BEACON_AFSTAND_M} m\n")
        f.write(f"# metingen per stap: {METINGEN_PER_STAP}\n")
        f.write(f"# beacon zendvermogen: {BEACON_TX_DBM} dBm\n")
        f.write(f"# lora config: {LORA_CONFIG}\n")
        f.write("# afstand_afgelegd = commando-afstand vanaf start; "
                "gps_* = werkelijke positie\n")

        kolommen = ['afstand_afgelegd', 'gps_lat', 'gps_lon', 'gps_alt',
                    'rssi_mediaan', 'rssi_min', 'rssi_max',
                    'snr_mediaan', 'snr_min', 'snr_max',
                    'n_metingen', 'tijdstip']
        schrijver = csv.DictWriter(f, fieldnames=kolommen)
        schrijver.writeheader()
        for rij in rijen:
            schrijver.writerow(rij)

    print(f"[approach] geschreven: {pad} ({len(rijen)} stappen)")
    return pad


def _samenvatting(rijen):
    """
    Log de kerngetallen: waar was de SNR maximaal (piek-positie), waar de
    RSSI, en het SNR-verloop. De piek-positie legt de operator naast de
    gemarkeerde beacon-positie om de systematische offset te bepalen.

    Returns een korte regel voor het dashboard.
    """
    g = [r for r in rijen if r['n_metingen'] > 0]
    if not g:
        print("[approach]   Geen enkel packet ontvangen tijdens de nadering.")
        return 'geen packets ontvangen'

    snr_piek = max(g, key=lambda r: r['snr_mediaan'])
    rssi_piek = max(g, key=lambda r: r['rssi_mediaan'])

    verloop = ' '.join(f"{r['snr_mediaan']:+.1f}" for r in g)
    print("[approach] --- Samenvatting nadering ---")
    print(f"[approach]   SNR-piek : {snr_piek['snr_mediaan']:+.2f} dB op "
          f"{snr_piek['afstand_afgelegd']} m afgelegd  "
          f"(GPS {snr_piek['gps_lat']:.7f}, {snr_piek['gps_lon']:.7f})")
    print(f"[approach]   RSSI-piek: {rssi_piek['rssi_mediaan']:.1f} dBm op "
          f"{rssi_piek['afstand_afgelegd']} m afgelegd")
    print(f"[approach]   beacon staat op {BEACON_AFSTAND_M} m -> SNR-piek ligt "
          f"{snr_piek['afstand_afgelegd'] - BEACON_AFSTAND_M:+.0f} m "
          f"t.o.v. de beacon")
    print(f"[approach]   SNR-verloop: {verloop}")

    # Duidelijke enkele piek? Ruwe check: is de piek een lokaal maximum met
    # daling aan beide kanten, en niet aan de rand van de reeks?
    idx = g.index(snr_piek)
    rand = idx == 0 or idx == len(g) - 1
    enkele = (not rand
              and g[idx - 1]['snr_mediaan'] < snr_piek['snr_mediaan']
              and g[idx + 1]['snr_mediaan'] < snr_piek['snr_mediaan'])
    if rand:
        vorm = 'piek ligt aan de RAND — mogelijk buiten het meetbereik'
    elif enkele:
        vorm = 'duidelijke enkele piek'
    else:
        vorm = 'geen duidelijke enkele piek (rommelig of plateau)'
    print(f"[approach]   vorm: {vorm}")

    return (f"SNR-piek {snr_piek['snr_mediaan']:+.1f} dB op "
            f"{snr_piek['afstand_afgelegd']} m "
            f"({snr_piek['afstand_afgelegd'] - BEACON_AFSTAND_M:+.0f} m "
            f"t.o.v. beacon); {vorm}")


# ============================================
# DE MEETSEQUENTIE
# ============================================

def _meet_op_plek(status, afstand, rijen):
    """Verzamel metingen op de huidige positie en voeg een rij toe."""
    rssi_w, snr_w, afgebroken = pattern._verzamel_metingen(status)

    rij = {
        'afstand_afgelegd': afstand,
        'gps_lat': round(status.get('gps_lat', 0.0), 7),
        'gps_lon': round(status.get('gps_lon', 0.0), 7),
        'gps_alt': round(status.get('altitude', 0.0), 2),
        'n_metingen': len(rssi_w),
        'tijdstip': datetime.now().strftime('%H:%M:%S'),
    }
    if rssi_w:
        rij.update({
            'rssi_mediaan': round(statistics.median(rssi_w), 2),
            'rssi_min': round(min(rssi_w), 2),
            'rssi_max': round(max(rssi_w), 2),
            'snr_mediaan': round(statistics.median(snr_w), 2),
            'snr_min': round(min(snr_w), 2),
            'snr_max': round(max(snr_w), 2),
        })
    else:
        rij.update({'rssi_mediaan': '', 'rssi_min': '', 'rssi_max': '',
                    'snr_mediaan': '', 'snr_min': '', 'snr_max': ''})
    rijen.append(rij)
    return rij, afgebroken


def _run_approach(status, get_mav, emit_fn, hoogte):
    """
    De volledige nadering. Draait in een aparte thread. Het veiligheidsmodel
    loopt door de HELE functie: na elke stap en tijdens elke wachttijd
    checken we of de piloot heeft overgenomen.
    """
    from pymavlink import mavutil

    stempel = datetime.now().strftime('%Y%m%d_%H%M%S')
    rijen = []
    start_gps = (0.0, 0.0)

    def afbreken_door_piloot():
        with _lock:
            approach_state['aborted_by_pilot'] = True
        _set_state('gestopt', 'Piloot heeft overgenomen — nadering gestopt',
                   active=False)
        emit_fn('meting_update', get_approach_state())

    try:
        # ---- pre-flight ----
        _set_state('preflight', 'Pre-flight check...')
        emit_fn('meting_update', get_approach_state())

        if get_mav() is None:
            _set_state('fout', 'Pixhawk niet verbonden', active=False)
            emit_fn('meting_update', get_approach_state())
            return

        sats = status.get('gps_satellites', 0)
        if REQUIRE_3D_FIX and not status.get('gps_fix', False):
            _set_state('fout', 'Geen 3D GPS-fix — meting afgebroken', active=False)
            emit_fn('meting_update', get_approach_state())
            return
        if sats < MIN_SATELLITES:
            _set_state('fout', f'Te weinig satellieten ({sats}/{MIN_SATELLITES}) '
                               f'— meting afgebroken', active=False)
            emit_fn('meting_update', get_approach_state())
            return

        laatste = status.get('lora_last_seen_sec', -1)
        if laatste < 0 or laatste > BEACON_MAX_AGE_S:
            _set_state('fout', 'Geen beaconsignaal — controleer of de beacon '
                               'aanstaat en zendt', active=False)
            emit_fn('meting_update', get_approach_state())
            return

        start_gps = (status.get('gps_lat', 0.0), status.get('gps_lon', 0.0))

        # ---- opstijgen ----
        _set_state('guided', 'GUIDED-mode instellen...')
        emit_fn('meting_update', get_approach_state())
        mission._cmd_set_mode_guided(get_mav, mavutil)
        if not pattern._wacht(2, status):
            afbreken_door_piloot(); return

        _set_state('arm', 'Armen...')
        emit_fn('meting_update', get_approach_state())
        mission._cmd_arm(get_mav, mavutil)
        if not pattern._wacht(3, status):
            afbreken_door_piloot(); return
        if not status.get('armed', False):
            _set_state('fout', 'Armen mislukt (check RC aan + GPS) — '
                               'meting afgebroken', active=False)
            emit_fn('meting_update', get_approach_state())
            return

        _set_state('takeoff', f'Opstijgen naar {hoogte} m...')
        emit_fn('meting_update', get_approach_state())
        mission._cmd_takeoff(get_mav, mavutil, hoogte)
        deadline = time.time() + TAKEOFF_TIMEOUT_S
        while time.time() < deadline:
            if pattern._pilot_has_taken_over(status):
                afbreken_door_piloot(); return
            if status.get('altitude', 0) >= hoogte * 0.95:
                break
            time.sleep(0.3)
        if not pattern._wacht(SETTLE_NA_KLIM_S, status):
            afbreken_door_piloot(); return

        # ---- nadering: meet op elke positie ----
        vorige_pos = 0.0
        for i, pos in enumerate(POSITIES_M):
            if pattern._pilot_has_taken_over(status):
                afbreken_door_piloot(); return

            delta = pos - vorige_pos
            if delta > 0:
                _set_state('vliegen', f'Vooruit naar {pos} m afgelegd...',
                           hoogte=hoogte, stap=i + 1)
                emit_fn('meting_update', get_approach_state())

                start_lat = status.get('gps_lat', 0.0)
                start_lon = status.get('gps_lon', 0.0)
                mission._cmd_forward(get_mav, mavutil, delta)

                voltooid, afgebroken = _wacht_op_stap(
                    status, start_lat, start_lon, delta)
                if afgebroken:
                    afbreken_door_piloot(); return
                if not voltooid:
                    print(f"[approach] stap naar {pos} m niet bevestigd binnen "
                          f"timeout — meet op de huidige positie")

                if not pattern._wacht(SETTLE_NA_STAP_S, status):
                    afbreken_door_piloot(); return

            _set_state('meten',
                       f'Meten op {pos} m afgelegd ({i + 1}/{len(POSITIES_M)})',
                       hoogte=hoogte, stap=i + 1)
            emit_fn('meting_update', get_approach_state())

            rij, afgebroken = _meet_op_plek(status, pos, rijen)
            print(f"[approach]   {pos:4.0f} m: "
                  f"RSSI {rij['rssi_mediaan']} dBm  SNR {rij['snr_mediaan']} dB  "
                  f"(n={rij['n_metingen']})")
            if afgebroken:
                afbreken_door_piloot(); return

            vorige_pos = pos

        # ---- terug naar huis ----
        _set_state('rtl', 'Nadering klaar — RTL naar opstijgpunt...')
        emit_fn('meting_update', get_approach_state())
        _cmd_rtl(get_mav)

    except Exception as e:
        _set_state('fout', f'Fout tijdens nadering: {e}', active=False)
        emit_fn('meting_update', get_approach_state())
        print(f"[approach] FOUT: {e}")

    finally:
        # Wat er ook gebeurd is: wegschrijven en samenvatten wat we hebben.
        samenvatting = ''
        if rijen:
            _schrijf_csv(hoogte, rijen, stempel, start_gps)
            samenvatting = _samenvatting(rijen)

        with _lock:
            afgebroken = approach_state['aborted_by_pilot']
            fout = approach_state['step'] == 'fout'

        if not afgebroken and not fout:
            _set_state('klaar', f'Nadering voltooid — {samenvatting or "geen data"}',
                       active=False)
        else:
            with _lock:
                approach_state['active'] = False

        emit_fn('meting_update', get_approach_state())


# ============================================
# PUBLIEKE API (aangeroepen vanuit app.py)
# ============================================

def start_meting(status, get_mav, emit_fn, hoogte=None):
    """
    Start de vooruit-nadering in een aparte thread.

    Weigert te starten als er al een nadering, een rotatiemeting (pattern.py)
    of een missie (mission.py) loopt: twee threads die tegelijk MAVLink naar
    dezelfde drone sturen is niet iets wat je in de lucht wilt uitzoeken.

    Returns (success: bool, message: str) voor directe feedback.
    """
    global _thread

    with _lock:
        if approach_state['active']:
            return False, 'Er loopt al een nadering'

    if pattern.get_meting_state().get('active'):
        return False, 'Er loopt een rotatiemeting — wacht tot die klaar is'
    if mission.get_mission_state().get('active'):
        return False, 'Er loopt een missie — wacht tot die klaar is'

    h = _clamp_hoogte(hoogte)

    with _lock:
        approach_state['aborted_by_pilot'] = False
        approach_state['hoogte'] = h
        approach_state['stap'] = 0

    _thread = threading.Thread(
        target=_run_approach,
        args=(status, get_mav, emit_fn, h),
        daemon=True,
        name='beacon-nadering'
    )
    _thread.start()
    return True, f'Nadering gestart ({h} m)'
