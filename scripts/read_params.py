#!/usr/bin/env python3
"""
Lees de fence- en RTL-parameters uit.

Matcht het antwoord op de gevraagde parameternaam in plaats van blind
het eerstvolgende PARAM_VALUE te pakken. Dat is nodig omdat de Pixhawk
ook ongevraagd PARAM_VALUE stuurt (bv. na een param_set), waardoor een
naïeve leeslus antwoorden verkeerd toewijst.
"""
from pymavlink import mavutil
import time

DEVICE = '/dev/serial/by-id/usb-Holybro_Pixhawk6C_32003E001651333337363133-if00'
NAMEN = ['RTL_ALT', 'FENCE_ALT_MAX', 'FENCE_ENABLE',
         'FENCE_TYPE', 'FENCE_ACTION']

m = mavutil.mavlink_connection(DEVICE, baud=115200)
m.wait_heartbeat(timeout=10)
print(f'Heartbeat OK, systeem {m.target_system}\n')

for naam in NAMEN:
    m.mav.param_request_read_send(
        m.target_system, m.target_component, naam.encode('utf-8'), -1)

    # Blijf lezen tot het antwoord met de JUISTE naam binnenkomt
    gevonden = None
    deadline = time.time() + 3
    while time.time() < deadline:
        msg = m.recv_match(type='PARAM_VALUE', blocking=True, timeout=1)
        if msg is None:
            continue
        if msg.param_id.replace(chr(0), '') == naam:
            gevonden = msg.param_value
            break

    if gevonden is None:
        print(f'  {naam:15s} = GEEN ANTWOORD')
    else:
        print(f'  {naam:15s} = {gevonden}')
