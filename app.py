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

from flask import Flask, render_template, jsonify
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

            mav.mav.request_data_stream_send(
                mav.target_system, mav.target_component,
                mavutil.mavlink.MAV_DATA_STREAM_ALL, 4, 1
            )

            while True:
                msg = mav.recv_match(blocking=True, timeout=5)
                if msg is None:
                    print("MAVLink timeout, herverbinden...")
                    status['pixhawk_connected'] = False
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