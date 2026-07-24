#!/usr/bin/env python3
"""
download_dataflash.py — haal de laatste vluchtlog van de Pixhawk.

ONTWIKKELGEREEDSCHAP. Bedoeld voor na een vlucht die anders liep dan
verwacht: de dataflash-log op de Pixhawk registreert modewissels MET REDEN,
failsafes en het spanningsverloop van de accu. Dat is de enige bron die
kan vertellen waarom de drone bijvoorbeeld zelf ging landen.

    sudo systemctl stop hornet-tracker
    python3 download_dataflash.py
    sudo systemctl start hornet-tracker

LET OP — TWEE TEGENGESTELDE EISEN, VERWAR ZE NIET:
  * De meting vanaf het dashboard (pattern.py) vereist dat de service
    JUIST DRAAIT.
  * Dit script vereist dat de service GESTOPT is, want het heeft de
    USB-poort naar de Pixhawk exclusief nodig.

TRAAG. De verbinding loopt op 115200 baud en het MAVLink-logprotocol
haalt in de praktijk grofweg 2-8 kB/s. Een vlucht van een paar minuten
levert al snel enkele megabytes op, dus reken op tientallen minuten.
De SD-kaart uit de Pixhawk halen en direct uitlezen is vele malen
sneller; doe dat als je meerdere logs of een lange vlucht nodig hebt.
Dit script bestaat voor het geval je de kaart niet kwijt wilt of er
niet bij kunt.
"""

import os
import subprocess
import sys
import time
from datetime import datetime

from pymavlink import mavutil

from app import MAVLINK_DEVICE, MAVLINK_BAUD

SERVICE_NAAM = 'hornet-tracker'
UITVOER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'data', 'dataflash')

# Hoe lang we op een antwoord van de Pixhawk wachten voor we opgeven.
LIJST_TIMEOUT_S = 20
BLOK_TIMEOUT_S = 10

# Grootte van één opgevraagd blok. 90 bytes is wat het protocol per
# LOG_DATA-bericht meestuurt; groter vragen levert niets extra's op.
BLOK = 90


def _service_draait():
    """
    Draait de systemd-service nog? Die houdt /dev/ttyACM0 bezet, dus dan
    kunnen wij er niet bij en krijg je een obscure I/O-fout.
    """
    r = subprocess.run(['systemctl', 'is-active', '--quiet', SERVICE_NAAM])
    return r.returncode == 0


def _formaat_grootte(bytes_):
    for eenheid in ['B', 'kB', 'MB']:
        if bytes_ < 1024 or eenheid == 'MB':
            return f'{bytes_:.1f} {eenheid}'
        bytes_ /= 1024


def haal_logboeklijst(mav):
    """Vraag de lijst met logs op. Returns dict {id: (grootte, tijd_utc)}."""
    mav.mav.log_request_list_send(mav.target_system, mav.target_component,
                                  0, 0xFFFF)
    logs = {}
    laatste = None
    deadline = time.time() + LIJST_TIMEOUT_S

    while time.time() < deadline:
        msg = mav.recv_match(type='LOG_ENTRY', blocking=True, timeout=2)
        if msg is None:
            # Niets meer binnen: als we al iets hebben, zijn we klaar.
            if logs:
                break
            continue
        if msg.num_logs == 0:
            return {}
        logs[msg.id] = (msg.size, msg.time_utc)
        laatste = msg.last_log_num
        if laatste is not None and msg.id >= laatste:
            break

    return logs


def download(mav, log_id, grootte, pad):
    """
    Haal één log op in blokken en schrijf hem weg.

    We vragen sequentieel op en accepteren alleen het blok op de offset die
    we verwachten. Dat is trager dan een sliding window, maar het houdt de
    code begrijpelijk en voorkomt gaten in het bestand — een half
    gedownloade log die er compleet uitziet is erger dan een trage download.
    """
    ontvangen = 0
    start = time.time()
    laatste_print = 0.0

    with open(pad, 'wb') as f:
        while ontvangen < grootte:
            mav.mav.log_request_data_send(
                mav.target_system, mav.target_component,
                log_id, ontvangen, BLOK)

            msg = mav.recv_match(type='LOG_DATA', blocking=True,
                                 timeout=BLOK_TIMEOUT_S)
            if msg is None:
                print(f"\n  Geen antwoord op offset {ontvangen} — opnieuw...")
                continue
            if msg.ofs != ontvangen:
                # Blok uit de pas; opnieuw vragen op onze eigen offset.
                continue

            brok = bytes(msg.data[:msg.count])
            f.write(brok)
            ontvangen += len(brok)

            if msg.count == 0:
                break   # Pixhawk heeft niets meer

            nu = time.time()
            if nu - laatste_print > 2.0:
                verstreken = nu - start
                snelheid = ontvangen / verstreken if verstreken else 0
                resterend = (grootte - ontvangen) / snelheid if snelheid else 0
                pct = 100.0 * ontvangen / grootte if grootte else 0
                print(f"\r  {pct:5.1f}%  {_formaat_grootte(ontvangen)} van "
                      f"{_formaat_grootte(grootte)}  "
                      f"{snelheid / 1024:.1f} kB/s  "
                      f"nog ~{resterend / 60:.0f} min", end='', flush=True)
                laatste_print = nu

    print()
    return ontvangen


def main():
    print("Pixhawk dataflash-log downloaden\n")

    if _service_draait():
        raise SystemExit(
            f"De service '{SERVICE_NAAM}' draait nog en houdt de USB-poort\n"
            f"naar de Pixhawk bezet.\n\n"
            f"    sudo systemctl stop {SERVICE_NAAM}\n"
            f"    python3 download_dataflash.py\n"
            f"    sudo systemctl start {SERVICE_NAAM}\n\n"
            f"(Let op: de meting vanaf het dashboard vereist juist dat de\n"
            f"service WEL draait — dit script is de uitzondering.)"
        )

    print(f"Verbinden met {MAVLINK_DEVICE} @ {MAVLINK_BAUD}...")
    mav = mavutil.mavlink_connection(MAVLINK_DEVICE, baud=MAVLINK_BAUD)
    if mav.wait_heartbeat(timeout=30) is None:
        raise SystemExit("Geen heartbeat van de Pixhawk — staat hij aan?")
    print(f"Heartbeat van systeem {mav.target_system}\n")

    print("Logboeklijst opvragen (kan even duren)...")
    logs = haal_logboeklijst(mav)
    if not logs:
        raise SystemExit("Geen logs gevonden op de Pixhawk.")

    nieuwste = max(logs)
    grootte, tijd_utc = logs[nieuwste]
    print(f"{len(logs)} logs gevonden. Nieuwste: #{nieuwste}, "
          f"{_formaat_grootte(grootte)}")

    # Ruwe schatting op basis van een realistische 4 kB/s.
    schatting = grootte / 4096 / 60
    print(f"\nGeschatte duur op 115200 baud: ~{schatting:.0f} minuten.")
    print("De SD-kaart uit de Pixhawk lezen is vele malen sneller.")
    antwoord = input("Doorgaan? [j/N] ").strip().lower()
    if antwoord != 'j':
        print("Afgebroken.")
        return 0

    os.makedirs(UITVOER_DIR, exist_ok=True)
    stempel = datetime.now().strftime('%Y%m%d_%H%M%S')
    pad = os.path.join(UITVOER_DIR, f'log_{nieuwste}_{stempel}.bin')

    print(f"\nDownloaden naar {pad}")
    try:
        ontvangen = download(mav, nieuwste, grootte, pad)
    except KeyboardInterrupt:
        print("\nOnderbroken — het deelbestand blijft staan.")
        return 1

    print(f"\nKlaar: {_formaat_grootte(ontvangen)} geschreven.")
    if ontvangen < grootte:
        print("LET OP: minder ontvangen dan verwacht; log is mogelijk "
              "onvolledig.")

    print("\nUitlezen met:")
    print(f"  ~/.local/bin/mavlogdump.py --types MODE,MSG,ERR,CURR,BAT {pad}")
    print("\n  MODE = modewissels met reden, ERR/MSG = failsafes, "
          "CURR/BAT = accuverloop.")
    print(f"\nVergeet niet: sudo systemctl start {SERVICE_NAAM}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
