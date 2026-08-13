#!/usr/bin/env python3
"""
Zet de hoogte-parameters voor de zoekmissie.

  RTL_ALT       500 cm  = 5 m   (was 400 = 4 m)
  FENCE_ALT_MAX  10 m           (was 5 m)

Reden: de zoekhoogte wordt instelbaar tot 5 m. De drone schommelde in
het veld ~1 m boven het setpoint, dus de fence moet daar ruim boven
liggen. RTL_ALT gelijk aan de max zoekhoogte zodat een RTL nooit lager
terugkeert dan waar de drone zat.

LET OP de eenheden: RTL_ALT is in centimeters, FENCE_ALT_MAX in meters.
Dat is een inconsistentie in ArduCopter zelf, geen typfout hier.

De service moet gestopt zijn — die claimt de MAVLink-poort.
"""
from pymavlink import mavutil
import time

DEVICE = '/dev/serial/by-id/usb-Holybro_Pixhawk6C_32003E001651333337363133-if00'

TE_ZETTEN = {
    'RTL_ALT':       (500.0, mavutil.mavlink.MAV_PARAM_TYPE_REAL32),
    'FENCE_ALT_MAX': (10.0,  mavutil.mavlink.MAV_PARAM_TYPE_REAL32),
}

TE_LEZEN = ['RTL_ALT', 'FENCE_ALT_MAX', 'FENCE_ENABLE',
            'FENCE_TYPE', 'FENCE_ACTION']

m = mavutil.mavlink_connection(DEVICE, baud=115200)
m.wait_heartbeat(timeout=10)
print(f'Heartbeat OK, systeem {m.target_system}')

for naam, (waarde, ptype) in TE_ZETTEN.items():
    m.mav.param_set_send(
        m.target_system, m.target_component,
        naam.encode('utf-8'), waarde, ptype)
    print(f'  {naam} -> {waarde}  (verstuurd)')
    time.sleep(0.5)

time.sleep(1)
print('\nVerificatie (uitlezen na schrijven):')
for naam in TE_LEZEN:
    m.mav.param_request_read_send(
        m.target_system, m.target_component, naam.encode('utf-8'), -1)
    msg = m.recv_match(type='PARAM_VALUE', blocking=True, timeout=3)
    if msg:
        print(f'  {msg.param_id.replace(chr(0), ""):15s} = {msg.param_value}')
    else:
        print(f'  {naam:15s} = GEEN ANTWOORD')
