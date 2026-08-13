#!/usr/bin/env python3
"""
End-to-end simulatie van de zoekvlucht.

Draaien:  python3 tests/test_vlucht.py
Schrijft naar tests/uitvoer/ — NOOIT naar data/.
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import approach
import search
from pymavlink import mavutil
from sim import Sim, draai_vlucht, uitvoermap, versnel

UIT = uitvoermap()
versnel()
fouten = []
ECHTE_DATA = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'data')
aantal_echt = len(glob.glob(os.path.join(ECHTE_DATA, 'search_*.csv')))


def check(naam, voorwaarde, detail=''):
    print(('  OK   ' if voorwaarde else '  FOUT ') + naam
          + (f'  [{detail}]' if detail else ''))
    if not voorwaarde:
        fouten.append(naam)


def nieuwste_csv():
    c = glob.glob(os.path.join(UIT, 'search_*.csv'))
    return max(c, key=os.path.getmtime) if c else None


# ============================================================
print('\n=== 1. NOMINALE VLUCHT — beacon op 30° / 20 m ===')
# ============================================================
sim = Sim(bearing=30.0, afstand=20.0)
meldingen, gelogd = draai_vlucht(sim)
staat = search.get_search_state()
print(f'  eind: {staat["step"]} — {staat["message"][:70]}')
check('eindigt in klaar', staat['step'] == 'klaar', staat['step'])
stappen = [m['step'] for m in meldingen]
for fase in ('hovertest', 'peilen', 'verifieren', 'doorvliegen',
             'terugkruisen', 'rtl'):
    check(f'fase {fase} doorlopen', fase in stappen)

print(f'  dichtste nadering tot de beacon: {sim.min_afstand:.2f} m')
check('drone kwam binnen 3 m van de beacon', sim.min_afstand < 3.0,
      f'{sim.min_afstand:.2f} m')
check('positie gelogd', len(gelogd) == 1, len(gelogd))
if gelogd:
    d = approach._horizontale_afstand(gelogd[0][0], gelogd[0][1],
                                      sim.blat, sim.blon)
    print(f'  gelogde positie: {d:.2f} m van de echte beacon')
    print(f'  notitie: {gelogd[0][2][:100]}')
    check('gelogde positie binnen 2 m', d < 2.0, f'{d:.2f} m')

# --- MAVLink-verkeer ---
yaw = [c for c in sim.commandos if c[0] == mavutil.mavlink.MAV_CMD_CONDITION_YAW]
check('geen relatieve (continue) draai meer', all(c[4] == 0 for c in yaw))
raster = sorted({round(c[1]) % 360 for c in yaw[:search.PEIL_AANTAL_STAPPEN]})
check('peilhoeken op het 30°-raster',
      all(h % search.PEIL_STAP_GRADEN == 0 for h in raster), raster)
check('alle 12 rasterhoeken bezocht',
      len(raster) == search.PEIL_AANTAL_STAPPEN, raster)
frames = {s['frame'] for s in sim.setpoints}
check('altijd LOCAL_OFFSET_NED',
      frames == {mavutil.mavlink.MAV_FRAME_LOCAL_OFFSET_NED}, frames)
check('type_mask gebruikt positie + yaw',
      {s['mask'] for s in sim.setpoints} == {0b0000101111111000})
check('z = 0 in elk setpoint (hoogte houden)',
      all(s['z'] == 0.0 for s in sim.setpoints))
wpnav = [p[1] for p in sim.params if p[0] == 'WPNAV_SPEED']
check('WPNAV_SPEED 50 -> 100', wpnav[:1] == [50.0] and wpnav[-1:] == [100.0], wpnav)
rtl = [p[1] for p in sim.params if p[0] == 'RTL_ALT']
check('RTL_ALT op zoekhoogte en terug op 500', rtl == [250.0, 500.0], rtl)
check('eindigt met RTL', ('set_mode', 'RTL') in sim.commandos)

pad = nieuwste_csv()
check('CSV weggeschreven', pad is not None)
if pad:
    inhoud = open(pad).read()
    for fase in ('peilen', 'verifieren', 'doorvliegen', 'terugkruisen'):
        check(f'CSV bevat fase {fase}', f'\n{fase},' in inhoud)
    check('CSV-kop bevat de hovertest', 'hovertest:' in inhoud)
    print('  --- CSV-kop ---')
    for regel in inhoud.splitlines():
        if regel.startswith('#'):
            print('   ', regel[:110])


# ============================================================
print('\n=== 2. ZIEK TOESTEL — hovertest moet de vlucht tegenhouden ===')
# ============================================================
sim2 = Sim(gezond=False)
meldingen2, gelogd2 = draai_vlucht(sim2)
staat2 = search.get_search_state()
print(f'  eind: {staat2["message"][:90]}')
check('LAND gecommandeerd', ('set_mode', 'LAND') in sim2.commandos)
check('geen RTL (we staan boven het opstijgpunt)',
      ('set_mode', 'RTL') not in sim2.commandos)
check('geen enkele peilstap gevlogen',
      not any(m['step'] == 'peilen' for m in meldingen2))
check('geen positie gelogd', len(gelogd2) == 0)
pad2 = nieuwste_csv()
check('meetwaarden van de afgekeurde hovertest toch bewaard',
      pad2 is not None and 'AFGEKEURD' in open(pad2).read())


# ============================================================
print('\n=== 3. PILOOT NEEMT OVER TIJDENS HET DOORVLIEGEN ===')
# ============================================================
sim3 = Sim(bearing=30.0, afstand=20.0)
meldingen3, gelogd3 = draai_vlucht(sim3, overname_na=25.0)
staat3 = search.get_search_state()
print(f'  eind: {staat3["step"]} — {staat3["message"][:60]}')
check('gestopt door piloot', staat3['step'] == 'gestopt', staat3['step'])
check('aborted_by_pilot gezet', staat3['aborted_by_pilot'])
check('geen RTL na overname — de zender is primair',
      ('set_mode', 'RTL') not in sim3.commandos)


# ============================================================
print('\n=== 4. ALLEEN REFLECTIES — kandidaten moeten verworpen worden ===')
# ============================================================
sim4 = Sim(reageert=False, tweede_lob=200.0)
meldingen4, gelogd4 = draai_vlucht(sim4)
staat4 = search.get_search_state()
print(f'  eind: {staat4["message"][:80]}')
check('valt terug op RTL', ('set_mode', 'RTL') in sim4.commandos)
check('geen positie gelogd', len(gelogd4) == 0)
check('meldt verworpen kandidaten',
      any('verworpen' in m.get('message', '') for m in meldingen4))


# ============================================================
na = len(glob.glob(os.path.join(ECHTE_DATA, 'search_*.csv')))
check(f'echte vluchtdata in data/ onaangeroerd ({aantal_echt} bestanden)',
      na == aantal_echt, f'{aantal_echt} -> {na}')
print('\n' + ('ALLE VLUCHTSCENARIOS OK' if not fouten
              else f'{len(fouten)} FOUT(EN): {fouten}'))
sys.exit(1 if fouten else 0)
