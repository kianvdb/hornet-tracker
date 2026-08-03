#!/usr/bin/env python3
"""
Eenheidstests voor de rekenkern van search.py — geen drone, geen threads.

Elke test hieronder hoort bij een fout die in het veld is opgetreden. De
verwijzingen naar vluchten zijn de logs uit data/ en LOGS/.

Draaien:  python3 tests/test_eenheden.py
"""
import math
import os
import statistics as st
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import search
from sim import uitvoermap

uitvoermap()          # search schrijft nooit in data/ tijdens tests

fouten = []


def check(naam, voorwaarde, detail=''):
    print(('  OK   ' if voorwaarde else '  FOUT ') + naam
          + (f'  [{detail}]' if detail else ''))
    if not voorwaarde:
        fouten.append(naam)


def dh(a, b):
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d


# ============================================================
print('\n=== GEOMETRIE ===')
# ============================================================
for hoek, verwacht in ((0, (10, 0)), (90, (0, 10)), (180, (-10, 0)), (270, (0, -10))):
    n, o = search._noord_oost(hoek, 10)
    check(f'{hoek}° = noord {verwacht[0]}, oost {verwacht[1]}',
          abs(n - verwacht[0]) < 1e-6 and abs(o - verwacht[1]) < 1e-6, (round(n, 3), round(o, 3)))


# ============================================================
print('\n=== PEILRASTER (12 x 30°) ===')
# ============================================================
r = search._peil_rooster(0.0)
check('12 hoeken', len(r) == 12, len(r))
check('raster 0,30,60..330', sorted(r) == [i * 30 for i in range(12)])
r2 = search._peil_rooster(200.0)
check('begint bij de dichtstbijzijnde hoek (200° -> 210°)', r2[0] == 210, r2[0])
check('loopt met de klok mee rond', r2[:4] == [210, 240, 270, 300], r2[:4])


# ============================================================
print('\n=== DRAAIRICHTING — kortste weg ===')
# Op alle drie de veldvluchten van 1-8 en 2-8 draaide de eerste peilstap
# bijna 360° de verkeerde kant op en werd hij MIDDEN IN DE DRAAI gemeten
# (commando 0°, gemeten op 71°, 92° en 75°).
# ============================================================
class _NepMav:
    target_system = 1
    target_component = 1
    def __init__(self):
        self.cmds = []
        self.mav = self
    def command_long_send(self, a, b, cmd, c, p1, p2, p3, p4, p5, p6, p7):
        self.cmds.append((p1, p3, p4))

from pymavlink import mavutil
for huidig, doel, verwacht in ((5, 0, -1), (0, 5, 1), (350, 10, 1), (10, 350, -1),
                               (71, 0, -1), (12, 60, 1)):
    m = _NepMav()
    search._cmd_yaw_kortste(lambda: m, mavutil, doel, huidig)
    p1, p3, p4 = m.cmds[0]
    check(f'{huidig}° -> {doel}° draait richting {verwacht:+d}', int(p3) == verwacht, int(p3))
    check(f'{huidig}° -> {doel}° is absoluut', int(p4) == 0)
graden = abs(((0 - 5 + 180) % 360) - 180)
check('de veldsituatie (5° -> 0°) kost nu 5° i.p.v. 355°', graden == 5, graden)
check('timeout dekt de grootste draai (180° bij 20°/s = 9 s)',
      180 / 20.0 < search.PEIL_HEADING_TIMEOUT_S, search.PEIL_HEADING_TIMEOUT_S)


# ============================================================
print('\n=== KANDIDAATKEUZE — zwaartepunt, niet de piek ===')
# De piek nemen faalde op 1-8 met 76° en 84°. Het zwaartepunt van dezelfde
# meetreeks zat er 1,8° en 5,7° naast.
# ============================================================
def lob(piek, breedte=40.0, top=8.0, bodem=-2.0):
    return [(i * 30.0, -95.0,
             bodem + (top - bodem) * math.exp(-(dh(i * 30.0, piek) / breedte) ** 2), 5)
            for i in range(12)]

k = search._kandidaten(lob(120.0))
check('enkele lob -> kandidaat op de bron', dh(k[0][0], 120.0) < 8, k[0][0])
check('enkele lob -> één kandidaat', len(k) == 1, len(k))

# Een tweede, ver weg gelegen sterke richting hoort TWEE kandidaten op te
# leveren: het zwaartepunt wordt er dan naartoe getrokken (de gedocumenteerde
# zwakte), en juist daarom blijft de piek als tweede hypothese bestaan zodat
# fase 2 ertussen beslist.
m = lob(120.0)
m[9] = (270.0, -90.0, 8.5, 5)
k = search._kandidaten(m)
check('tweede sterke richting -> twee kandidaten', len(k) == 2, len(k))
check('de piek zit erbij als tweede hypothese',
      any(dh(h, 270.0) < 1 for h, _ in k), [round(h) for h, _ in k])
check('nooit meer dan MAX_KANDIDATEN', len(k) <= search.MAX_KANDIDATEN, len(k))
check('lege invoer', search._kandidaten([]) == [])

# Het veld op 2-8 19:12: SNR vrijwel vlak van 62° tot 211°. Het zwaartepunt
# lag 11,5° van de beacon, de piek 19°.
veld = [(12.4, -103, 2.0, 5), (62.5, -100, 5.8, 5), (71.1, -101, 6.0, 5),
        (87.4, -102, 3.8, 5), (120.4, -99, 6.2, 5), (151.9, -98, 6.8, 5),
        (177.2, -100, 6.2, 5), (211.5, -99, 6.8, 5), (240.1, -102, 4.8, 5),
        (266.3, -104, 2.0, 5), (296.7, -102, 2.5, 5), (323.5, -102, 1.2, 5)]
zw = search._zwaartepunt(veld)
check('echte velddata 19:12: zwaartepunt binnen 15° van de beacon (133°)',
      dh(zw, 133.0) < 15, f'{zw:.1f}° -> {dh(zw, 133.0):.1f}° eraf')
piek = max(veld, key=lambda m: m[2])[0]
check('echte velddata 19:12: zwaartepunt beter dan de piek',
      dh(zw, 133.0) < dh(piek, 133.0), f'zwaartepunt {dh(zw,133.0):.1f}° vs piek {dh(piek,133.0):.1f}°')


# ============================================================
print('\n=== LOBDIAGNOSE ===')
# ============================================================
d = search._lob_diagnose(lob(120.0, breedte=25.0), [])
check('smalle lob -> SCHERP', 'SCHERP' in d, d[:60])
# Een vlak plateau zoals in het veld gemeten (2-8 19:12: 62°-211° binnen
# ~1 dB). Een gaussische lob is hiervoor te bol; dit is de echte vorm.
plateau = [(i * 30.0, -95.0, (7.0 if 60 <= i * 30 <= 210 else 1.0), 5)
           for i in range(12)]
d = search._lob_diagnose(plateau, [])
check('vlak plateau -> BREED, piek is ruis', 'BREED' in d, d[:75])
m = lob(120.0, breedte=25.0)
m[8] = (240.0, -95.0, 7.9, 5)
d = search._lob_diagnose(m, [])
check('tweede losse richting -> AMBIGU', 'AMBIGU' in d, d[:70])
check('lege invoer', 'geen metingen' in search._lob_diagnose([], []))


# ============================================================
print('\n=== INSTORT UIT EEN DOORLOPENDE VLUCHT ===')
# ============================================================
R = 111320.0
def pass_samples(beacon_op_m, lengte_m, richting=1, lateraal=0.0, start_m=0.0):
    s = []
    for i in range(int(lengte_m / 0.25)):
        d = start_m + richting * i * 0.25
        afst = math.hypot(d - beacon_op_m, lateraal)
        rssi = (-86.0 - 0.8 * max(0.0, afst - 6.0)
                - (9.0 * (1.0 - math.exp(-(afst / 3.0) ** 2)) if afst < 6 else 9.0))
        s.append({'lat': 50.759 + d / R, 'lon': 4.2256,
                  'rssi': round(rssi, 1), 'snr': round(rssi + 101, 1)})
    return s

res = search._zoek_instort(pass_samples(10.0, 16.0))
check('instort gevonden', res is not None and res[4] == 'instort', res and res[4])
check('vlakke reeks -> terugval op sterkste RSSI',
      search._zoek_instort([{'lat': 50.759 + i / R, 'lon': 4.2256,
                             'rssi': -90.0, 'snr': 11.0} for i in range(40)])[4] == 'rssi')
check('lege reeks -> None', search._zoek_instort([]) is None)


# ============================================================
print('\n=== TERUGKRUISING — twee kruisingen middelen de vertraging weg ===')
# ============================================================
B = (50.759 + 10.0 / R, 4.2256)
def afst(a, b):
    return math.hypot((b[0] - a[0]) * R, 0)
print('  lateraal   heen     terug    MIDDELPUNT')
for lat_off in (0.0, 1.0, 2.0, 3.0):
    ih = search._zoek_instort(pass_samples(10.0, 16.0, +1, lat_off, 0.0))
    it = search._zoek_instort(pass_samples(10.0, 16.0, -1, lat_off, 16.0))
    c = search._combineer_instorten(ih, it)
    fh, ft, fc = afst((ih[0], 0), (B[0], 0)), afst((it[0], 0), (B[0], 0)), afst((c[0], 0), (B[0], 0))
    print(f'   {lat_off:5.1f} m   {fh:5.2f} m  {ft:5.2f} m   {fc:5.2f} m')
    check(f'middelpunt beter dan beide enkele kruisingen ({lat_off} m lateraal)',
          fc < min(fh, ft), f'{fc:.2f} < {min(fh, ft):.2f}')

# betrouwbaarheid volgt de spreiding
def nep(methode, offset):
    return (B[0] + offset / R, B[1], 'hoog', 'test', methode)
for spr, m1, m2, verwacht in ((1.0, 'instort', 'instort', 'hoog'),
                              (5.0, 'instort', 'instort', 'midden'),
                              (9.0, 'instort', 'instort', 'laag'),
                              (1.0, 'instort', 'rssi', 'midden')):
    r = search._combineer_instorten(nep(m1, 0.0), nep(m2, spr))
    check(f'{spr:.0f} m spreiding, {m1}/{m2} -> {verwacht}', r[2] == verwacht, r[2])
check('één kruising blijft bruikbaar',
      'één kruising' in search._combineer_instorten(nep('instort', 0.0), None)[3])
check('geen kruisingen -> None', search._combineer_instorten(None, None) is None)


# ============================================================
print('\n=== HOVERTEST ===')
# Na de val van 2-8 21:26. De motoren stonden toen 103 PWM uit elkaar tijdens
# rustig stilhangen; de trilling ging van 26 naar 114 vlak vóór het verlies
# van controle.
# ============================================================
search.HOVERTEST_DUUR_S = 0.4
def toestand(vx, vy, vz, pwm, clip=0, mode='GUIDED'):
    return {'flight_mode': mode, 'vibe_x': vx, 'vibe_y': vy, 'vibe_z': vz,
            'vibe_clip': clip, 'motor_pwm': pwm}

for naam, s, verwacht_ok in (
        ('gezond', toestand(12, 15, 14, [1650, 1660, 1680, 1670]), True),
        ('crashvlucht 2-8 (26 trilling, 103 PWM)',
         toestand(13, 26, 25, [1623, 1708, 1725, 1626]), True),
        ('trilling 70', toestand(20, 70, 30, [1650, 1660, 1680, 1670]), False),
        ('motoren 200 PWM scheef', toestand(12, 15, 14, [1500, 1700, 1600, 1550]), False),
        ('geen telemetrie', toestand(-1, -1, -1, []), True)):
    ok, tekst, meting = search._hovertest(s)
    check(f'{naam}: {"door" if verwacht_ok else "afgekeurd"}', ok == verwacht_ok, tekst[:70])

ok, tekst, _ = search._hovertest(toestand(13, 26, 25, [1623, 1708, 1725, 1626]))
check('crashvlucht krijgt wel de waarschuwing', 'motoren staan scheef' in tekst, tekst[:70])

# clipping is een OPLOPENDE teller: de toename tijdens het venster telt
s = toestand(12, 15, 14, [1650, 1660, 1680, 1670], clip=7)
threading.Thread(target=lambda: (time.sleep(0.15), s.update({'vibe_clip': 10})),
                 daemon=True).start()
ok, tekst, meting = search._hovertest(s)
check('toename van de clipping-teller keurt af', not ok and meting['clip'] == 3, meting)
ok2, _, _ = search._hovertest(toestand(12, 15, 14, [1650, 1660, 1680, 1670], clip=7))
check('oude clipping (constante teller) keurt niet af', ok2)
ok3, tekst3, _ = search._hovertest(toestand(12, 15, 14, [1650, 1660, 1680, 1670], mode='LOITER'))
check('piloot-overname wordt gedetecteerd', not ok3 and 'piloot' in tekst3, tekst3)


print('\n' + ('ALLE EENHEIDSTESTS OK' if not fouten
              else f'{len(fouten)} FOUT(EN): {fouten}'))
sys.exit(1 if fouten else 0)
