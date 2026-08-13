#!/usr/bin/env python3
"""
sim.py — nep-drone en nep-MAVLink voor het testen van search.py.

Waarom dit bestaat: search.py stuurt een echte drone aan. Zonder simulator is
elke wijziging pas te toetsen door te vliegen, en dat kost een accu per poging
plus het risico dat een fout pas in de lucht blijkt.

SIGNAALMODEL, geijkt op de zes meetvluchten:
  - lobverlies 0..9 dB over de hoek. In de rotatiemetingen varieerde RSSI
    9-11 dB over de hoek en SNR 7-10 dB, met r = 0,89..0,94: de antennelob
    zit dus in BEIDE, niet alleen in de SNR.
  - padverlies 0,8 dB/m boven 6 m (log 165: -93 dBm op 15 m, -86 op 6 m)
  - extra verlies onder 6 m, zodat er een instort te vinden is bij het passeren
  - SNR = RSSI + 101, geplafonneerd op 9 dB

WAT DIT WEL TEST: faseovergangen, welke MAVLink-berichten met welke velden
uitgaan, de beslisregels, het wegschrijven bij afbreken.
WAT DIT NIET TEST: of ArduCopter LOCAL_OFFSET_NED + yaw doet wat wij denken,
de echte timing tussen de LoRa- en MAVLink-threads, en hoe de signaal-instort
er bij een echte gladde pass uitziet. Daar is hardware voor nodig.
"""
import math
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import approach
import mission
import pattern
import search

M_PER_GRAAD = 111320.0
SIM_SNELHEIDSFACTOR = 10.0   # 1 m/s in de Pixhawk -> 10 m/s in de simulatie


def uitvoermap():
    """Testbestanden gaan NOOIT naar data/ — daar staat echte vluchtdata."""
    pad = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uitvoer')
    os.makedirs(pad, exist_ok=True)
    search.DATA_DIR = pad
    return pad


def versnel():
    """
    Kort de wachttijden in zodat een vlucht van vier minuten in seconden
    doorloopt. Het AANTAL stappen en metingen blijft gelijk, zodat de
    simulatie hetzelfde pad aflegt als het echte algoritme.
    """
    pattern.YAW_RATE_DPS = 200.0
    pattern.PACKET_TIMEOUT_S = 1.0
    for naam, waarde in dict(
            SETTLE_NA_PEILSTAP_S=0.15, PEIL_HEADING_TIMEOUT_S=3.0,
            PEIL_MAX_DUUR_S=300.0, AANKONDIGING_S=0.2, SETTLE_NA_KLIM_S=0.2,
            SETTLE_NA_STAP_S=0.2, SETTLE_NA_DRAAI_S=0.2,
            YAW_SETTLE_VOOR_PASS_S=0.3, TAKEOFF_TIMEOUT_S=4.0,
            DRAAI_TIMEOUT_S=3.0, HOVERTEST_DUUR_S=0.5).items():
        setattr(search, naam, waarde)
    approach.STAP_BASIS_TIMEOUT_S = 1.0
    approach.STAP_RAMING_SNELHEID = 8.0
    # _wacht_op_stap eist 3 positiemonsters binnen dit venster en pollt elke
    # 0,3 s. Korter dan ~1 s halen die 3 monsters het nooit.
    approach.STAP_STABIEL_DUUR_S = 1.2


class Sim:
    """Nep-drone met een beacon op een bekende plek."""

    def __init__(self, bearing=30.0, afstand=20.0, reageert=True,
                 tweede_lob=None, gezond=True):
        self.status = {
            'flight_mode': 'STABILIZE', 'armed': False, 'altitude': 0.0,
            'heading': 0.0, 'gps_lat': 50.7590500, 'gps_lon': 4.2256300,
            'gps_fix': True, 'gps_satellites': 12, 'battery_percent': 95,
            'lora_packet_count': 0, 'lora_last_seen_sec': 1,
            'signal_power': -100.0, 'lora_snr': 0.0,
        }
        self.zet_toestand(gezond)
        self.slat = self.status['gps_lat']
        self.slon = self.status['gps_lon']
        dn, de = search._noord_oost(bearing, afstand)
        self.blat = self.slat + dn / M_PER_GRAAD
        self.blon = self.slon + de / (M_PER_GRAAD * math.cos(
            math.radians(self.slat)))
        # reageert=False bootst een reflectie na: het signaal heeft wel een
        # richting maar verandert niet als je ernaartoe vliegt.
        self.reageert = reageert
        self.tweede_lob = tweede_lob
        self.vaste_bearing = bearing

        self.doel_lat = self.doel_lon = None
        self.doel_hoogte = 0.0
        self.yaw_doel = None
        self.yaw_rate = 30.0
        self.yaw_rest = 0.0
        self.wpnav = 100.0
        self.commandos = []
        self.setpoints = []
        self.params = []
        self.min_afstand = 1e9
        self.stop = False
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def zet_toestand(self, gezond):
        """Trilling en motorbalans voor de hovertest."""
        if gezond:
            self.status.update({'vibe_x': 12, 'vibe_y': 15, 'vibe_z': 14,
                                'vibe_clip': 0,
                                'motor_pwm': [1650, 1660, 1680, 1670]})
        else:
            self.status.update({'vibe_x': 20, 'vibe_y': 75, 'vibe_z': 30,
                                'vibe_clip': 0,
                                'motor_pwm': [1500, 1700, 1600, 1550]})

    def afstand_tot_beacon(self):
        return approach._horizontale_afstand(
            self.status['gps_lat'], self.status['gps_lon'],
            self.blat, self.blon)

    def bearing_naar_beacon(self):
        dn = (self.blat - self.status['gps_lat']) * M_PER_GRAAD
        de = (self.blon - self.status['gps_lon']) * M_PER_GRAAD * math.cos(
            math.radians(self.status['gps_lat']))
        return math.degrees(math.atan2(de, dn)) % 360

    def signaal(self):
        d = self.afstand_tot_beacon()
        bearing = (self.bearing_naar_beacon() if self.reageert
                   else self.vaste_bearing)
        delta = search._hoekverschil(self.status['heading'], bearing)
        lobverlies = 9.0 * (1.0 - math.exp(-(delta / 40.0) ** 2))
        if self.tweede_lob is not None:
            d2 = search._hoekverschil(self.status['heading'], self.tweede_lob)
            lobverlies = min(lobverlies,
                             9.0 * (1.0 - math.exp(-(d2 / 40.0) ** 2)) + 2.0)
        padverlies = 0.8 * max(0.0, d - 6.0) if self.reageert else 7.0
        dichtbij = 9.0 * (1.0 - math.exp(-(d / 3.0) ** 2)) if d < 6 else 9.0
        rssi = -86.0 - padverlies - lobverlies - (dichtbij if self.reageert else 0.0)
        return rssi, min(9.0, rssi + 101.0)

    def _loop(self):
        dt = 0.02
        volgend_packet = time.time()
        while not self.stop:
            time.sleep(dt)
            if self.doel_hoogte > self.status['altitude']:
                self.status['altitude'] = min(
                    self.doel_hoogte, self.status['altitude'] + 5.0 * dt)

            if self.yaw_rest > 0:
                stap = min(self.yaw_rest, self.yaw_rate * dt)
                self.status['heading'] = (self.status['heading'] + stap) % 360
                self.yaw_rest -= stap
            elif self.yaw_doel is not None:
                verschil = ((self.yaw_doel - self.status['heading'] + 180) % 360) - 180
                if abs(verschil) > 0.05:
                    stap = max(-self.yaw_rate * dt,
                               min(self.yaw_rate * dt, verschil))
                    self.status['heading'] = (self.status['heading'] + stap) % 360

            if self.doel_lat is not None:
                rest = approach._horizontale_afstand(
                    self.status['gps_lat'], self.status['gps_lon'],
                    self.doel_lat, self.doel_lon)
                snelheid = self.wpnav / 100.0 * SIM_SNELHEIDSFACTOR
                if rest <= snelheid * dt:
                    self.status['gps_lat'] = self.doel_lat
                    self.status['gps_lon'] = self.doel_lon
                    self.doel_lat = self.doel_lon = None
                else:
                    f = snelheid * dt / rest
                    self.status['gps_lat'] += (self.doel_lat - self.status['gps_lat']) * f
                    self.status['gps_lon'] += (self.doel_lon - self.status['gps_lon']) * f

            self.min_afstand = min(self.min_afstand, self.afstand_tot_beacon())

            if time.time() >= volgend_packet:
                volgend_packet = time.time() + 0.05
                rssi, snr = self.signaal()
                self.status['signal_power'] = round(rssi, 1)
                self.status['lora_snr'] = round(snr, 1)
                self.status['lora_packet_count'] += 1

    def start(self):
        self._thread.start()

    def get_mav(self):
        return NepMav(self)


class NepBerichten:
    def __init__(self, sim):
        self.sim = sim

    def command_long_send(self, s, c, cmd, conf, p1, p2, p3, p4, p5, p6, p7):
        from pymavlink import mavutil
        self.sim.commandos.append((cmd, p1, p2, p3, p4, p7))
        if cmd == mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM:
            self.sim.status['armed'] = bool(p1)
        elif cmd == mavutil.mavlink.MAV_CMD_NAV_TAKEOFF:
            self.sim.doel_hoogte = p7
        elif cmd == mavutil.mavlink.MAV_CMD_CONDITION_YAW:
            self.sim.yaw_rate = p2
            if p4:                       # relatief
                self.sim.yaw_rest = p1
            else:                        # absoluut
                self.sim.yaw_rest = 0.0
                self.sim.yaw_doel = p1 % 360

    def set_position_target_local_ned_send(self, t, s, c, frame, mask,
                                           x, y, z, vx, vy, vz,
                                           ax, ay, az, yaw, yaw_rate):
        self.sim.setpoints.append({'frame': frame, 'mask': mask, 'noord': x,
                                   'oost': y, 'z': z,
                                   'yaw_deg': math.degrees(yaw) % 360})
        self.sim.doel_lat = self.sim.status['gps_lat'] + x / M_PER_GRAAD
        self.sim.doel_lon = self.sim.status['gps_lon'] + y / (
            M_PER_GRAAD * math.cos(math.radians(self.sim.status['gps_lat'])))
        self.sim.yaw_doel = math.degrees(yaw) % 360

    def param_set_send(self, s, c, naam, waarde, ptype):
        naam = naam.decode() if isinstance(naam, bytes) else naam
        self.sim.params.append((naam, waarde))
        if naam == 'WPNAV_SPEED':
            self.sim.wpnav = waarde


class NepMav:
    target_system = 1
    target_component = 1

    def __init__(self, sim):
        self.sim = sim
        self.mav = NepBerichten(sim)

    def set_mode(self, naam):
        self.sim.status['flight_mode'] = naam
        self.sim.commandos.append(('set_mode', naam))
        if naam == 'RTL':
            self.sim.doel_lat = self.sim.slat
            self.sim.doel_lon = self.sim.slon


def draai_vlucht(sim, hoogte=2.5, timeout=180, overname_na=None):
    """
    Start een zoekvlucht en wacht tot hij klaar is.

    Returns (meldingen, gelogde_posities). overname_na simuleert de piloot
    die na N seconden de SwC-switch omzet.
    """
    meldingen, gelogd = [], []
    sim.start()

    def emit(naam, data):
        if naam == 'meting_update':
            meldingen.append(dict(data))

    def log_fn(lat, lon, alt, notities):
        gelogd.append((lat, lon, notities))

    search.start_search(sim.status, sim.get_mav, emit, hoogte=hoogte,
                        log_fn=log_fn)
    begin = time.time()
    while time.time() - begin < timeout:
        if overname_na is not None and time.time() - begin > overname_na:
            sim.status['flight_mode'] = 'LOITER'
            overname_na = None
        if not search.get_search_state()['active']:
            break
        time.sleep(0.1)
    time.sleep(0.5)
    sim.stop = True
    return meldingen, gelogd
