#!/usr/bin/env python3
"""
Hornet Tracker - Ground Station Server (v3 "ultimate")

Strategie:
  - Persistent RTL-SDR (geen rtl_power subprocess, geen re-init)
  - Peak-hold detectie over 400ms window -> pakt beacon packets (~200ms TX)
  - Vaste 1-malige baseline (uit v1, werkte het beste)
  - Smalle 200 kHz detectie-band -> minder ruis buiten
  - Abstracte SignalSource interface -> Ra-01 LoRa ontvanger plug-in klaar

Toekomstige LoRa ontvanger (Ra-01 via SPI):
  1. Implementeer LoRaSource class (unten in file, nu als stub)
  2. Zet SIGNAL_SOURCE = 'lora' bovenaan
  3. Restart service -> dashboard werkt identiek
  Alles daarbuiten (WiFi, MAVLink, dashboard) blijft onveranderd.

Kalibratie van beacon v2 (zie beacon.ino):
  - SF9, BW 125 kHz, 433 MHz, ~200ms time-on-air, elke 500ms
"""

from flask import Flask, render_template, jsonify, request, send_file
import io
import json
from flask_socketio import SocketIO
import subprocess
import threading
import time
import os
import re
import numpy as np

# ============================================
# CONFIGURATIE
# ============================================

# --- Bron selectie (FUTURE-PROOF) ---
# 'rtlsdr' = huidige RTL-SDR detectie (energy detection)
# 'lora'   = Ra-01 SPI ontvanger (packet decoding) -- nog te implementeren
SIGNAL_SOURCE = 'rtlsdr'

# --- RTL-SDR parameters ---
CENTER_FREQ = 433.0e6        # Beacon frequentie (Hz)
SAMPLE_RATE = 2.048e6        # RTL-SDR native rate (Hz)
GAIN = 49.6                  # dB
NUM_SAMPLES = 16384          # per meting (~8ms data)

DETECT_BANDWIDTH = 200e3     # Smal band rond center -> filter ruis
THRESHOLD = 6                # dB boven baseline = detectie

# --- Peak-hold parameters ---
# Beacon v2 zendt ~200ms elke 500ms, dus window van 400ms pakt hem gegarandeerd
PEAK_HOLD_SAMPLES = 50       # 50 x 8ms = ~400ms window
MEASURE_INTERVAL = 0.1       # 10 Hz update naar dashboard

# --- Baseline (vast, na initiele meting) ---
BASELINE_NUM_READINGS = 10   # aantal metingen voor initiele baseline

# --- MAVLink ---
MAVLINK_DEVICE = '/dev/ttyPixhawk'
MAVLINK_BAUD = 57600

# --- WiFi hotspot ---
WIFI_HOTSPOT_IFACE = 'wlan1'

# --- Persistente log storage ---
# JSON-bestand op disk i.p.v. browser-localStorage zodat log overleeft
# browser-refresh, andere browsers, andere devices, en Pi-reboots.
# Atomic writes via temp-file + os.replace zodat een crash midden in
# een write geen corrupte file achterlaat.
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
LOG_FILE = os.path.join(DATA_DIR, 'coord-log.json')
log_lock = threading.Lock()

# --- Tile cache ---
# Map waar offline tiles worden opgeslagen, gestructureerd als
#   data/tiles/<source>/<z>/<x>/<y>.png
# Twee sources: 'osm' (OpenStreetMap stratenplan) en 'sat' (ArcGIS satelliet)
TILE_CACHE_DIR = os.path.join(DATA_DIR, 'tiles')

# Externe tile-servers — gebruikt als fallback bij cache-miss met internet.
# {z}/{x}/{y} placeholders worden vervangen door python format string.
TILE_SOURCES = {
    'osm': 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    'sat': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    # NB: ArcGIS gebruikt {z}/{y}/{x} (omgekeerde volgorde), niet typo
}

# User-agent vereist door OSM tile-server policy. Beleefd identificeren
# voorkomt dat ze ons IP blokkeren bij prefetch van veel tiles.
TILE_USER_AGENT = 'VespaTrack/1.0 (bachelorthesis Erasmushogeschool Brussel)'

# ============================================
# FLASK APP
# ============================================
app = Flask(__name__)
app.config['SECRET_KEY'] = 'hornet-tracker-secret'
socketio = SocketIO(app, cors_allowed_origins="*")

status = {
    'signal_power': -100,
    'signal_delta': 0,
    'signal_detected': False,
    'baseline': -100,
    'signal_source': SIGNAL_SOURCE,  # dashboard kan tonen welke bron actief is

    'pi_connected': True,
    'pixhawk_connected': False,

    'gps_lat': 0, 'gps_lon': 0, 'gps_fix': False, 'gps_satellites': 0,
    'altitude': 0,
    'battery_voltage': 0, 'battery_percent': 0,
    'flight_mode': 'UNKNOWN', 'armed': False,

    'wifi_connected': False, 'wifi_rssi': -100, 'wifi_quality': 0, 'wifi_clients': 0,

    'telem_connected': False,
    'telem_rssi_local': 0, 'telem_rssi_remote': 0,
    'telem_noise_local': 0, 'telem_noise_remote': 0,
    'telem_quality': 0, 'telem_txbuf': 0,

    # LoRa-specifieke velden (stub voor toekomst)
    'lora_packet_count': 0,
    'lora_last_tracker_id': 0,
    'lora_last_seen_sec': -1,
    'lora_snr': 0,
}

baseline_reset_requested = False

# Globale MAVLink connectie - gezet door mavlink_loop() na succesvolle connect.
# Wordt gebruikt door command handlers (arm/disarm/set_mode) om commando's
# naar de Pixhawk te sturen. None betekent: geen verbinding actief.
mav_connection = None
mav_lock = threading.Lock()

# ============================================
# COORDINATE LOG STORAGE (JSON op disk)
# ============================================
#
# Het gelogde-coordinaten-bestand is de "source of truth" voor alle
# entries. Frontend leest het via GET /api/log bij page-load en
# wijzigt het via POST/PUT/DELETE endpoints.
#
# Bestandsformaat: lijst van entry-objects, top-level array.
#   [
#     {"id": "abc123", "lat": 50.7, "lon": 4.3, "alt": 0,
#      "time": "15:44:22", "date": "2026-05-20T13:44:22.829Z",
#      "source": "manueel"|"drone",
#      "status": "gemeld"|"wordt_onderzocht"|"waargenomen"|"bestreden"|"vals_alarm"|"",
#      "notes": ""},
#     ...
#   ]
#
# ID is een server-side gegenereerde unieke string (timestamp+random).
# Frontend gebruikt deze voor PUT/DELETE in plaats van array-index zodat
# delete + concurrent edit niet de verkeerde entry raakt.

def ensure_data_dir():
    """Maak data/ aan als hij niet bestaat (bv. eerste run)."""
    os.makedirs(DATA_DIR, exist_ok=True)


def load_log():
    """
    Lees coord-log uit JSON-bestand. Returns lege lijst als bestand niet
    bestaat of corrupt is. Thread-safe via log_lock.
    """
    with log_lock:
        if not os.path.exists(LOG_FILE):
            return []
        try:
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            print(f"!! coord-log.json bevat geen lijst, return leeg")
            return []
        except (json.JSONDecodeError, IOError) as e:
            print(f"!! coord-log.json lezen mislukt: {e}, return leeg")
            return []


def save_log(entries):
    """
    Schrijf coord-log atomisch naar disk: write naar .tmp file, dan
    os.replace() naar definitieve naam. os.replace is atomair op POSIX
    zodat het bestand nooit half-geschreven op disk staat bij een crash.
    Thread-safe via log_lock.
    """
    with log_lock:
        ensure_data_dir()
        tmp_path = LOG_FILE + '.tmp'
        try:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(entries, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, LOG_FILE)
            return True
        except IOError as e:
            print(f"!! coord-log.json schrijven mislukt: {e}")
            return False


def generate_entry_id():
    """
    Genereer een unieke string-ID voor een nieuwe entry.
    Gebruikt timestamp (ms) + 4 random hex chars zodat collisions
    onmogelijk zijn bij realistische gebruiks-rate.
    """
    import secrets
    timestamp_ms = int(time.time() * 1000)
    random_hex = secrets.token_hex(2)
    return f"{timestamp_ms}-{random_hex}"


    # ============================================
# TILE CACHE (offline maps)
# ============================================
#
# Tiles worden lokaal opgeslagen onder data/tiles/<source>/<z>/<x>/<y>.png.
# De serve_tile route hierboven probeert eerst lokaal, valt terug op
# internet wanneer een tile niet gecached is.
#
# Atomic writes: download naar .tmp, dan os.replace zodat een crashed
# download geen corrupte PNG achterlaat.

def tile_cache_path(source, z, x, y):
    """Bouw het filesystem-pad voor een tile in onze cache."""
    return os.path.join(TILE_CACHE_DIR, source, str(z), str(x), f"{y}.png")


def fetch_tile_from_internet(source, z, x, y):
    """
    Haal een tile op bij de externe tile-server. Returns (bytes, content_type)
    of (None, None) bij fout (netwerk, 404, blokkering).

    Slaat het resultaat NIET zelf op — dat doet de aanroeper, zodat
    fetch en cache-write apart te debuggen zijn.
    """
    import urllib.request
    import urllib.error

    if source not in TILE_SOURCES:
        return None, None

    url = TILE_SOURCES[source].format(z=z, x=x, y=y)
    req = urllib.request.Request(url, headers={'User-Agent': TILE_USER_AGENT})

    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            data = response.read()
            content_type = response.headers.get('Content-Type', 'image/png')
            return data, content_type
    except urllib.error.URLError as e:
        # Netwerk down, geen internet, of tile server unreachable
        print(f"[tiles] fetch failed {source}/{z}/{x}/{y}: {e}")
        return None, None
    except Exception as e:
        print(f"[tiles] unexpected error {source}/{z}/{x}/{y}: {e}")
        return None, None


def save_tile_to_cache(source, z, x, y, data):
    """
    Schrijf een tile atomisch naar de cache. Maakt parent-directories aan
    indien nodig. Returns True bij succes.
    """
    path = tile_cache_path(source, z, x, y)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + '.tmp'
        with open(tmp_path, 'wb') as f:
            f.write(data)
        os.replace(tmp_path, path)
        return True
    except IOError as e:
        print(f"[tiles] save failed {source}/{z}/{x}/{y}: {e}")
        return False


# ============================================
# SIGNAL SOURCE INTERFACE (abstract)
# ============================================
class SignalSource:
    """
    Abstracte klasse. Subclasses implementeren:
      - open()                : init hardware
      - close()               : cleanup
      - measure_once() -> dB  : 1 single narrow-band meting in dB
      - describe() -> str     : voor logging

    Voor LoRa ontvanger kan measure_once() simpelweg de laatste
    gedecodeerde packet RSSI returnen, met detected flag in status.
    """
    def open(self): raise NotImplementedError
    def close(self): pass
    def measure_once(self): raise NotImplementedError
    def describe(self): return "unknown"


# ============================================
# RTL-SDR SIGNAL SOURCE
# ============================================
class RtlSdrSource(SignalSource):
    def __init__(self):
        self.sdr = None

    def open(self):
        from rtlsdr import RtlSdr
        while self.sdr is None:
            try:
                print("RTL-SDR openen...")
                sdr = RtlSdr()
                sdr.sample_rate = SAMPLE_RATE
                sdr.center_freq = CENTER_FREQ
                sdr.gain = GAIN
                sdr.read_samples(NUM_SAMPLES)  # warmup
                self.sdr = sdr
                print(f"RTL-SDR OK: {SAMPLE_RATE/1e6:.2f} MSPS @ {CENTER_FREQ/1e6:.3f} MHz, gain {GAIN} dB")
            except Exception as e:
                print(f"RTL-SDR fout: {e}. Retry over 3s...")
                time.sleep(3)

    def close(self):
        if self.sdr:
            try: self.sdr.close()
            except Exception: pass
            self.sdr = None

    def measure_once(self):
        try:
            samples = self.sdr.read_samples(NUM_SAMPLES)
        except Exception as e:
            print(f"SDR read error: {e}, reopen...")
            self.close()
            self.open()
            return None

        # FFT, shift zodat DC in midden zit
        fft = np.fft.fftshift(np.fft.fft(samples))
        power = np.abs(fft) ** 2

        bin_hz = SAMPLE_RATE / len(samples)
        center_bin = len(samples) // 2
        half_bins = int((DETECT_BANDWIDTH / 2) / bin_hz)

        # DC-spike vermijden (±2 bins rond center)
        dc_gap = 2
        left = power[center_bin - half_bins : center_bin - dc_gap]
        right = power[center_bin + dc_gap : center_bin + half_bins]
        narrow = np.concatenate([left, right])
        if len(narrow) == 0:
            return -100.0

        peak = float(np.max(narrow))
        return 10 * np.log10(peak + 1e-12)

    def describe(self):
        return f"RTL-SDR @ {CENTER_FREQ/1e6:.3f} MHz, BW {DETECT_BANDWIDTH/1e3:.0f} kHz"


# ============================================
# LORA SIGNAL SOURCE (stub - later implementeren)
# ============================================
class LoRaSource(SignalSource):
    """
    Stub voor Ra-01 SPI ontvanger.

    Implementatie later (wanneer Ra-01 gesoldeerd en aangesloten):
      pip install pyLoRa-spi   (of spidev + eigen driver)

      def open(self):
          from pyLoRa_spi import LoRa
          self.lora = LoRa(spi_bus=0, spi_cs=0, pin_reset=24, pin_dio0=25)
          self.lora.set_frequency(433.0)
          self.lora.set_spreading_factor(9)       # MATCH beacon.ino
          self.lora.set_bandwidth(125e3)
          self.lora.set_coding_rate(5)
          self.lora.set_crc(True)
          self.lora.set_sync_word(0x12)
          self.lora.receive()

      def measure_once(self):
          packet = self.lora.read_packet(timeout=0.4)
          if packet is None:
              return None                # geen packet = "geen signaal"
          # packet format: "HT,<id>,<count>,<padding>"
          parts = packet.decode().split(',')
          if parts[0] != 'HT':
              return None
          status['lora_packet_count'] += 1
          status['lora_last_tracker_id'] = int(parts[1])
          status['lora_last_seen_sec'] = 0
          status['lora_snr'] = self.lora.last_snr
          return self.lora.last_rssi    # echte dBm!
    """
    def open(self):
        print("!! LoRaSource nog niet geimplementeerd. Wacht op Ra-01 hardware.")
        print("!! Val terug op RTL-SDR.")
        raise NotImplementedError("LoRaSource requires Ra-01 SPI receiver")

    def describe(self):
        return "LoRa Ra-01 (stub)"


def make_signal_source():
    if SIGNAL_SOURCE == 'lora':
        try:
            src = LoRaSource()
            src.open()
            return src
        except NotImplementedError:
            pass  # fallback
    src = RtlSdrSource()
    src.open()
    return src


# ============================================
# SIGNAL LOOP (peak-hold + vaste baseline)
# ============================================
def signal_loop():
    global status, baseline_reset_requested

    source = make_signal_source()
    status['signal_source'] = source.describe()
    print(f"Signal source: {source.describe()}")

    def do_baseline():
        print("Baseline meting...")
        socketio.emit('baseline_status', {'measuring': True})
        readings = []
        for i in range(BASELINE_NUM_READINGS):
            # Gebruik peak-hold ook voor baseline -> consistent
            peaks = []
            for _ in range(PEAK_HOLD_SAMPLES):
                v = source.measure_once()
                if v is not None:
                    peaks.append(v)
            if peaks:
                readings.append(max(peaks))
                print(f"  Baseline {i+1}/{BASELINE_NUM_READINGS}: {readings[-1]:.1f} dB")
        baseline = sum(readings) / len(readings) if readings else -100
        print(f"Baseline: {baseline:.1f} dB (vast)")
        socketio.emit('baseline_status', {'measuring': False, 'baseline': round(baseline, 1)})
        return baseline

    baseline = do_baseline()
    status['baseline'] = round(baseline, 1)

    while True:
        try:
            if baseline_reset_requested:
                baseline_reset_requested = False
                baseline = do_baseline()
                status['baseline'] = round(baseline, 1)

            # Peak-hold: verzamel PEAK_HOLD_SAMPLES metingen, neem max
            peaks = []
            for _ in range(PEAK_HOLD_SAMPLES):
                v = source.measure_once()
                if v is not None:
                    peaks.append(v)

            if not peaks:
                time.sleep(MEASURE_INTERVAL)
                continue

            peak_power = max(peaks)
            delta = peak_power - baseline
            detected = delta > THRESHOLD

            status['signal_power'] = round(peak_power, 1)
            status['signal_delta'] = round(delta, 1)
            status['signal_detected'] = detected

            socketio.emit('status_update', status)
            time.sleep(MEASURE_INTERVAL)

        except Exception as e:
            print(f"Signal loop error: {e}")
            time.sleep(1)


# ============================================
# WIFI RSSI (ongewijzigd)
# ============================================
def read_wifi_status():
    try:
        result = subprocess.run(
            ['iw', 'dev', WIFI_HOTSPOT_IFACE, 'station', 'dump'],
            capture_output=True, text=True, timeout=3
        )
        output = result.stdout
        if not output.strip():
            return {'connected': False, 'rssi': -100, 'quality': 0, 'clients': 0}
        clients = len(re.findall(r'^Station ', output, re.MULTILINE))
        signals = [int(m) for m in re.findall(r'signal:\s*(-?\d+)', output)]
        if not signals:
            return {'connected': clients > 0, 'rssi': -100, 'quality': 0, 'clients': clients}
        best_rssi = max(signals)
        quality = max(0, min(100, int((best_rssi + 90) * 100 / 60)))
        return {'connected': True, 'rssi': best_rssi, 'quality': quality, 'clients': clients}
    except FileNotFoundError:
        return {'connected': False, 'rssi': -100, 'quality': 0, 'clients': 0}
    except Exception as e:
        print(f"WiFi status error: {e}")
        return {'connected': False, 'rssi': -100, 'quality': 0, 'clients': 0}


def wifi_loop():
    global status
    while True:
        try:
            wifi = read_wifi_status()
            status['wifi_connected'] = wifi['connected']
            status['wifi_rssi'] = wifi['rssi']
            status['wifi_quality'] = wifi['quality']
            status['wifi_clients'] = wifi['clients']
            time.sleep(2)
        except Exception as e:
            print(f"WiFi loop error: {e}")
            time.sleep(2)


# ============================================
# MAVLINK (ongewijzigd)
# ============================================
def mavlink_loop():
    global status
    try:
        from pymavlink import mavutil
    except ImportError:
        print("!! pymavlink niet geinstalleerd")
        return

    COPTER_MODES = {
        0: 'STABILIZE', 1: 'ACRO', 2: 'ALT_HOLD', 3: 'AUTO',
        4: 'GUIDED', 5: 'LOITER', 6: 'RTL', 7: 'CIRCLE',
        9: 'LAND', 11: 'DRIFT', 13: 'SPORT', 14: 'FLIP',
        15: 'AUTOTUNE', 16: 'POSHOLD', 17: 'BRAKE', 18: 'THROW',
        19: 'AVOID_ADSB', 20: 'GUIDED_NOGPS', 21: 'SMART_RTL',
        22: 'FLOWHOLD', 23: 'FOLLOW', 24: 'ZIGZAG', 25: 'SYSTEMID',
        26: 'AUTOROTATE', 27: 'AUTO_RTL'
    }
    last_radio_status = 0

    while True:
        try:
            print(f"MAVLink verbinden met {MAVLINK_DEVICE} @ {MAVLINK_BAUD}...")
            mav = mavutil.mavlink_connection(MAVLINK_DEVICE, baud=MAVLINK_BAUD)
            mav.wait_heartbeat(timeout=10)
            print(f"MAVLink OK! System {mav.target_system}, Component {mav.target_component}")
            status['pixhawk_connected'] = True
            # Maak connectie beschikbaar voor command handlers
            global mav_connection
            with mav_lock:
                mav_connection = mav

            mav.mav.request_data_stream_send(
                mav.target_system, mav.target_component,
                mavutil.mavlink.MAV_DATA_STREAM_ALL, 4, 1
            )

            while True:
                msg = mav.recv_match(blocking=True, timeout=5)
                if msg is None:
                    print("MAVLink timeout, herverbinden...")
                    status['pixhawk_connected'] = False
                    with mav_lock:
                        mav_connection = None
                    break
                msg_type = msg.get_type()
                now = time.time()

                if msg_type == 'HEARTBEAT':
                    status['pixhawk_connected'] = True
                    status['armed'] = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                    status['flight_mode'] = COPTER_MODES.get(msg.custom_mode, f'MODE_{msg.custom_mode}')
                elif msg_type == 'SYS_STATUS':
                    status['battery_voltage'] = round(msg.voltage_battery / 1000.0, 2)
                    if msg.battery_remaining >= 0:
                        status['battery_percent'] = msg.battery_remaining
                elif msg_type == 'BATTERY_STATUS':
                    if msg.voltages[0] != 65535:
                        status['battery_voltage'] = round(msg.voltages[0] / 1000.0, 2)
                    if msg.battery_remaining >= 0:
                        status['battery_percent'] = msg.battery_remaining
                elif msg_type == 'GPS_RAW_INT':
                    status['gps_fix'] = msg.fix_type >= 3
                    status['gps_satellites'] = msg.satellites_visible
                elif msg_type == 'GLOBAL_POSITION_INT':
                    status['gps_lat'] = msg.lat / 1e7
                    status['gps_lon'] = msg.lon / 1e7
                    status['altitude'] = round(msg.relative_alt / 1000.0, 2)
                elif msg_type in ('RADIO_STATUS', 'RADIO'):
                    last_radio_status = now
                    status['telem_connected'] = True
                    status['telem_rssi_local'] = round(msg.rssi / 1.9 - 127, 1)
                    status['telem_rssi_remote'] = round(msg.remrssi / 1.9 - 127, 1)
                    status['telem_noise_local'] = round(msg.noise / 1.9 - 127, 1)
                    status['telem_noise_remote'] = round(msg.remnoise / 1.9 - 127, 1)
                    status['telem_txbuf'] = msg.txbuf
                    worst_snr = min(msg.rssi - msg.noise, msg.remrssi - msg.remnoise)
                    status['telem_quality'] = max(0, min(100, int(worst_snr * 100 / 50)))

                if now - last_radio_status > 5:
                    status['telem_connected'] = False
        except Exception as e:
            print(f"MAVLink error: {e}")
            status['pixhawk_connected'] = False
            status['telem_connected'] = False
            time.sleep(5)


# ============================================
# ROUTES / EVENTS
# ============================================
@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/api/status')
def get_status():
    return jsonify(status)


@app.route('/api/export-xlsx', methods=['POST'])
def export_xlsx():
    """
    Genereer een gestileerd Excel-bestand uit een lijst log-entries.

    Frontend POST't JSON met:
      {
        "entries": [
          {"lat": ..., "lon": ..., "alt": ..., "time": ..., "date": ...,
           "source": "drone"|"manueel", "status": "...", "notes": "..."},
          ...
        ],
        "filename": "vespatrack_log_20-05-2026_17h30.xlsx"
      }

    Returns het XLSX-bestand als binary download.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    payload = request.get_json(silent=True) or {}
    entries = payload.get('entries', [])
    filename = payload.get('filename', 'vespatrack_log.xlsx')

    # --- Workbook + sheet aanmaken ---
    wb = Workbook()
    ws = wb.active
    ws.title = "Hornet log"

    # --- Headers definiëren ---
    headers = [
        '#', 'Tijd', 'Datum', 'Bron', 'Status',
        'Latitude', 'Longitude', 'Hoogte (m)', 'Notitie', 'Google Maps'
    ]

    # --- Header-stijl: vet wit op donkerblauw, gecentreerd ---
    header_font = Font(name='Calibri', size=11, bold=True, color='FFFFFFFF')
    header_fill = PatternFill(start_color='FF16213E', end_color='FF16213E', fill_type='solid')
    header_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    thin_border = Border(
        left=Side(style='thin', color='FFCCCCCC'),
        right=Side(style='thin', color='FFCCCCCC'),
        top=Side(style='thin', color='FFCCCCCC'),
        bottom=Side(style='thin', color='FFCCCCCC')
    )

    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin_border

    ws.row_dimensions[1].height = 28

    # --- Status-kleuren (afgestemd op dashboard CSS) ---
    status_fills = {
        'gemeld':           PatternFill(start_color='FFE5E7EB', end_color='FFE5E7EB', fill_type='solid'),
        'wordt_onderzocht': PatternFill(start_color='FFFEF3C7', end_color='FFFEF3C7', fill_type='solid'),
        'waargenomen':      PatternFill(start_color='FFD1FAE5', end_color='FFD1FAE5', fill_type='solid'),
        'bestreden':        PatternFill(start_color='FFA7F3D0', end_color='FFA7F3D0', fill_type='solid'),
        'vals_alarm':       PatternFill(start_color='FFE5E7EB', end_color='FFE5E7EB', fill_type='solid'),
    }
    status_labels = {
        'gemeld':           'Gemeld',
        'wordt_onderzocht': 'Wordt onderzocht',
        'waargenomen':      'Waargenomen',
        'bestreden':        'Bestreden',
        'vals_alarm':       'Vals alarm',
        '':                 '',
    }
    source_labels = {
        'drone':   '🚁 drone',
        'manueel': '📍 manueel',
    }

    # --- Data rows ---
    for row_idx, e in enumerate(entries, start=2):
        lat = float(e.get('lat', 0))
        lon = float(e.get('lon', 0))
        alt = float(e.get('alt', 0))
        gmaps_url = f"https://www.google.com/maps?q={lat},{lon}"
        status = e.get('status', '')
        source = e.get('source', '')

        ws.cell(row=row_idx, column=1, value=row_idx - 1)
        ws.cell(row=row_idx, column=2, value=e.get('time', ''))
        ws.cell(row=row_idx, column=3, value=e.get('date', ''))
        ws.cell(row=row_idx, column=4, value=source_labels.get(source, source))
        ws.cell(row=row_idx, column=5, value=status_labels.get(status, status))
        ws.cell(row=row_idx, column=6, value=lat)
        ws.cell(row=row_idx, column=7, value=lon)
        ws.cell(row=row_idx, column=8, value=alt)
        ws.cell(row=row_idx, column=9, value=e.get('notes', ''))

        # Google Maps hyperlink
        maps_cell = ws.cell(row=row_idx, column=10, value='Open in Maps')
        maps_cell.hyperlink = gmaps_url
        maps_cell.font = Font(color='FF0563C1', underline='single')

        # Status-cel kleur
        if status in status_fills:
            ws.cell(row=row_idx, column=5).fill = status_fills[status]

        # Borders + alignment voor alle cellen in deze rij
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.border = thin_border
            if col_idx in (1, 2, 3, 4, 5, 6, 7, 8):
                cell.alignment = Alignment(horizontal='center', vertical='center')
            else:
                cell.alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)

        # Lat/lon met 7 decimalen formatteren
        ws.cell(row=row_idx, column=6).number_format = '0.0000000'
        ws.cell(row=row_idx, column=7).number_format = '0.0000000'
        ws.cell(row=row_idx, column=8).number_format = '0.00'

    # --- Kolombreedtes ---
    column_widths = {
        1: 5,    # #
        2: 11,   # Tijd
        3: 24,   # Datum (ISO)
        4: 14,   # Bron
        5: 18,   # Status
        6: 13,   # Latitude
        7: 13,   # Longitude
        8: 11,   # Hoogte
        9: 35,   # Notitie
        10: 16,  # Google Maps link
    }
    for col_idx, width in column_widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    # --- Freeze panes (header rij vastzetten bij scroll) ---
    ws.freeze_panes = 'A2'

    # --- Schrijf naar in-memory buffer + stuur als download ---
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    return send_file(
        buffer,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

    # ============================================
# REST ENDPOINTS — coördinaat log
# ============================================

@app.route('/api/log', methods=['GET'])
def api_log_get():
    """
    Haal alle gelogde entries op. Wordt door frontend aangeroepen bij
    page-load om de log te tonen.
    """
    entries = load_log()
    return jsonify(entries)


@app.route('/api/log', methods=['POST'])
def api_log_post():
    """
    Voeg een nieuwe entry toe. Verwacht JSON body:
      {lat, lon, alt, time, date, source, status, notes}
    Server genereert het 'id' veld en voegt de entry achteraan toe.
    Returns de aangemaakte entry inclusief id.
    """
    payload = request.get_json(silent=True) or {}

    # Minimaal validatie — lat/lon zijn vereist
    if 'lat' not in payload or 'lon' not in payload:
        return jsonify({'error': 'lat en lon zijn vereist'}), 400

    entry = {
        'id':     generate_entry_id(),
        'lat':    float(payload.get('lat', 0)),
        'lon':    float(payload.get('lon', 0)),
        'alt':    float(payload.get('alt', 0)),
        'time':   payload.get('time', ''),
        'date':   payload.get('date', ''),
        'source': payload.get('source', 'manueel'),
        'status': payload.get('status', ''),
        'notes':  payload.get('notes', ''),
    }

    entries = load_log()
    entries.append(entry)
    if save_log(entries):
        return jsonify(entry), 201
    return jsonify({'error': 'opslaan mislukt'}), 500


@app.route('/api/log/<entry_id>', methods=['PUT'])
def api_log_put(entry_id):
    """
    Bewerk een bestaande entry. Identificatie via stabiele server-side
    ID, niet via array-index. Body bevat de velden die gewijzigd worden;
    onbenoemde velden blijven ongewijzigd. Returns de bijgewerkte entry.
    """
    payload = request.get_json(silent=True) or {}

    entries = load_log()
    for entry in entries:
        if entry.get('id') == entry_id:
            # Update alleen toegestane velden
            for field in ('status', 'notes', 'lat', 'lon', 'alt'):
                if field in payload:
                    if field in ('lat', 'lon', 'alt'):
                        entry[field] = float(payload[field])
                    else:
                        entry[field] = payload[field]
            if save_log(entries):
                return jsonify(entry)
            return jsonify({'error': 'opslaan mislukt'}), 500

    return jsonify({'error': f'entry {entry_id} niet gevonden'}), 404


@app.route('/api/log/<entry_id>', methods=['DELETE'])
def api_log_delete(entry_id):
    """
    Verwijder één entry op basis van stabiele ID.
    Returns 204 No Content bij succes, 404 als de entry niet bestaat.
    """
    entries = load_log()
    new_entries = [e for e in entries if e.get('id') != entry_id]

    if len(new_entries) == len(entries):
        return jsonify({'error': f'entry {entry_id} niet gevonden'}), 404

    if save_log(new_entries):
        return '', 204
    return jsonify({'error': 'opslaan mislukt'}), 500


@app.route('/api/log', methods=['DELETE'])
def api_log_clear():
    """
    Wis alle entries (equivalent van 'Wissen' knop in dashboard).
    Returns 204 No Content.
    """
    if save_log([]):
        return '', 204
    return jsonify({'error': 'opslaan mislukt'}), 500

    # ============================================
# TILE ROUTE — lokaal-eerst, internet-fallback
# ============================================

@app.route('/tiles/<source>/<int:z>/<int:x>/<int:y>.png')
def serve_tile(source, z, x, y):
    """
    Lever een map-tile aan de browser. Twee stappen:

      1. Probeer uit lokale cache (data/tiles/...)
      2. Bij cache-miss: download van externe server, sla op in cache,
         lever aan browser

    Bij geen internet en geen cache: 404. Frontend Leaflet behandelt
    dit als "tile niet beschikbaar" en toont een grijs vlak.
    """
    if source not in TILE_SOURCES:
        return jsonify({'error': f'onbekende source: {source}'}), 400

    # 1. Cache check
    path = tile_cache_path(source, z, x, y)
    if os.path.exists(path):
        # Detecteer JPEG-magic-bytes (ArcGIS levert JPEG) vs PNG
        with open(path, 'rb') as f:
            magic = f.read(3)
        mime = 'image/jpeg' if magic[:3] == b'\xff\xd8\xff' else 'image/png'
        return send_file(path, mimetype=mime)

    # 2. Cache miss — probeer internet
    data, content_type = fetch_tile_from_internet(source, z, x, y)
    if data is None:
        # Geen internet of tile niet bestaand op server
        return '', 404

    # Sla op voor toekomstig gebruik (best-effort, faal stil als disk vol)
    save_tile_to_cache(source, z, x, y, data)

    # Lever direct aan browser zonder een tweede disk-read
    return data, 200, {'Content-Type': content_type}


@app.route('/api/tiles/stats')
def api_tile_stats():
    """
    Statistieken over de tile cache: aantal tiles per source, totale
    disk-grootte. Wordt door dashboard gebruikt om operator te informeren
    hoeveel offline-kaart-data lokaal beschikbaar is.
    """
    stats = {}
    if not os.path.exists(TILE_CACHE_DIR):
        return jsonify({'sources': {}, 'total_bytes': 0, 'total_count': 0})

    total_bytes = 0
    total_count = 0

    for source in TILE_SOURCES.keys():
        source_dir = os.path.join(TILE_CACHE_DIR, source)
        if not os.path.exists(source_dir):
            stats[source] = {'count': 0, 'bytes': 0}
            continue

        count = 0
        size = 0
        for root, dirs, files in os.walk(source_dir):
            for f in files:
                if f.endswith('.png'):
                    count += 1
                    try:
                        size += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
        stats[source] = {'count': count, 'bytes': size}
        total_bytes += size
        total_count += count

    return jsonify({
        'sources': stats,
        'total_bytes': total_bytes,
        'total_count': total_count,
    })

@socketio.on('connect')
def handle_connect():
    print('Client verbonden')
    socketio.emit('status_update', status)

@socketio.on('disconnect')
def handle_disconnect():
    print('Client verbroken')

@socketio.on('reset_baseline')
def handle_reset_baseline():
    global baseline_reset_requested
    print('Baseline reset aangevraagd')
    baseline_reset_requested = True

# ============================================
# DRONE COMMAND HANDLERS (frontend → Pixhawk)
# ============================================
#
# Elk commando volgt hetzelfde patroon:
#   1. check of mav_connection bestaat (Pixhawk verbonden?)
#   2. stuur COMMAND_LONG via pymavlink
#   3. wacht max 5s op COMMAND_ACK met match op het juiste command-id
#   4. emit 'command_result' terug naar frontend met success + message
#
# De frontend (drone-controls.js) heeft al een 5s timeout met fallback-
# bericht 'Geen reactie van Pi/Pixhawk', dus deze handlers hoeven daar
# niet over te zorgen — alleen valide responses sturen.

COMMAND_ACK_TIMEOUT = 5.0   # seconden

def send_command_and_wait_ack(command_id, params, command_name):
    """
    Stuur een MAV_CMD via command_long_send en wacht op COMMAND_ACK.

    Returns (success: bool, message: str). Geschikt om direct in
    socketio.emit('command_result', ...) te dumpen.
    """
    from pymavlink import mavutil

    with mav_lock:
        mav = mav_connection

    if mav is None:
        return False, "Pixhawk niet verbonden"

    try:
        # COMMAND_LONG met 7 params (sommige worden 0 gelaten als niet gebruikt)
        mav.mav.command_long_send(
            mav.target_system,
            mav.target_component,
            command_id,
            0,    # confirmation
            params[0], params[1], params[2], params[3],
            params[4], params[5], params[6],
        )

        # Wacht op ACK voor exact dit commando-id
        ack = mav.recv_match(
            type='COMMAND_ACK',
            blocking=True,
            timeout=COMMAND_ACK_TIMEOUT,
            condition=f'COMMAND_ACK.command=={command_id}'
        )

        if ack is None:
            return False, f"Geen ACK ontvangen voor {command_name}"

        result_codes = {
            mavutil.mavlink.MAV_RESULT_ACCEPTED:  (True,  f"{command_name} geaccepteerd"),
            mavutil.mavlink.MAV_RESULT_DENIED:    (False, f"{command_name} geweigerd door Pixhawk"),
            mavutil.mavlink.MAV_RESULT_UNSUPPORTED: (False, f"{command_name} niet ondersteund"),
            mavutil.mavlink.MAV_RESULT_FAILED:    (False, f"{command_name} mislukt"),
            mavutil.mavlink.MAV_RESULT_TEMPORARILY_REJECTED: (False, f"{command_name} tijdelijk geweigerd (check arming/GPS)"),
        }
        return result_codes.get(ack.result, (False, f"Onbekende ACK: {ack.result}"))

    except Exception as e:
        return False, f"MAVLink fout: {e}"


@socketio.on('arm_drone')
def handle_arm_drone():
    """Vraag Pixhawk om te armen. MAV_CMD_COMPONENT_ARM_DISARM met param1=1."""
    from pymavlink import mavutil
    print('ARM commando ontvangen van frontend')
    success, message = send_command_and_wait_ack(
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        [1, 0, 0, 0, 0, 0, 0],   # param1=1 = arm
        'ARM'
    )
    socketio.emit('command_result', {'success': success, 'message': message})


@socketio.on('disarm_drone')
def handle_disarm_drone():
    """Vraag Pixhawk om te disarmen. MAV_CMD_COMPONENT_ARM_DISARM met param1=0."""
    from pymavlink import mavutil
    print('DISARM commando ontvangen van frontend')
    success, message = send_command_and_wait_ack(
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        [0, 0, 0, 0, 0, 0, 0],   # param1=0 = disarm
        'DISARM'
    )
    socketio.emit('command_result', {'success': success, 'message': message})


@socketio.on('set_mode')
def handle_set_mode(data):
    """
    Wijzig flight mode. Frontend stuurt {'mode': <int>} met ArduCopter mode-ID
    uit COPTER_MODES (zie mavlink_loop). Gebruikt mav.set_mode() helper i.p.v.
    raw COMMAND_LONG omdat pymavlink dat voor ArduCopter beter afhandelt.
    """
    from pymavlink import mavutil
    mode_id = data.get('mode')
    print(f'SET_MODE commando ontvangen: mode_id={mode_id}')

    with mav_lock:
        mav = mav_connection

    if mav is None:
        socketio.emit('command_result', {
            'success': False,
            'message': 'Pixhawk niet verbonden'
        })
        return

    try:
        # ArduCopter mode wijzigen via set_mode helper
        mav.set_mode(mode_id)

        # Wacht op HEARTBEAT met de nieuwe custom_mode als bevestiging
        # (ArduCopter stuurt geen COMMAND_ACK voor mode-changes via set_mode)
        import time
        deadline = time.time() + COMMAND_ACK_TIMEOUT
        confirmed = False
        while time.time() < deadline:
            msg = mav.recv_match(type='HEARTBEAT', blocking=True, timeout=1)
            if msg and msg.custom_mode == mode_id:
                confirmed = True
                break

        if confirmed:
            socketio.emit('command_result', {
                'success': True,
                'message': f'Mode gewijzigd naar ID {mode_id}'
            })
        else:
            socketio.emit('command_result', {
                'success': False,
                'message': f'Mode-wijziging niet bevestigd binnen {COMMAND_ACK_TIMEOUT}s'
            })

    except Exception as e:
        socketio.emit('command_result', {
            'success': False,
            'message': f'MAVLink fout: {e}'
        })

@socketio.on('shutdown')
def handle_shutdown():
    print('Shutdown aangevraagd')
    socketio.emit('shutdown_status', {'message': 'Pi wordt afgesloten...'})
    time.sleep(1)
    os.system('sudo shutdown now')

@socketio.on('reboot')
def handle_reboot():
    print('Reboot aangevraagd')
    socketio.emit('shutdown_status', {'message': 'Pi wordt herstart...'})
    time.sleep(1)
    os.system('sudo reboot')


if __name__ == '__main__':
    print("=" * 50)
    print("Hornet Tracker Ground Station v3 (ultimate)")
    print(f"  Source: {SIGNAL_SOURCE}")
    print(f"  Peak-hold: {PEAK_HOLD_SAMPLES} samples (~{PEAK_HOLD_SAMPLES*NUM_SAMPLES/SAMPLE_RATE*1000:.0f} ms window)")
    print(f"  Detect BW: {DETECT_BANDWIDTH/1e3:.0f} kHz")
    print(f"  Threshold: +{THRESHOLD} dB")
    print("=" * 50)

    threading.Thread(target=signal_loop, daemon=True).start()
    threading.Thread(target=wifi_loop, daemon=True).start()
    threading.Thread(target=mavlink_loop, daemon=True).start()

    print("Dashboard: http://192.168.1.6:5000")
    print("          http://192.168.4.1:5000")
    print("=" * 50)
    socketio.run(app, host='0.0.0.0', port=5000,
                 debug=False, allow_unsafe_werkzeug=True)