#!/usr/bin/env python3
from pymavlink import mavutil
import time

SERIAL_PORT = '/dev/ttyUSB0'
BAUD_RATE = 57600

def connect_pixhawk():
    print("Verbinden met Pixhawk...")
    try:
        mav = mavutil.mavlink_connection(SERIAL_PORT, baud=BAUD_RATE)
        mav.wait_heartbeat(timeout=10)
        print(f"Verbonden! System ID: {mav.target_system}")
        return mav
    except Exception as e:
        print(f"Fout: {e}")
        return None

def monitor_motors(mav, duration=60):
    print("\n" + "="*50)
    print("MOTOR OUTPUT MONITOR")
    print("="*50)
    
    # Request ALL data streams
    mav.mav.request_data_stream_send(
        mav.target_system,
        mav.target_component,
        mavutil.mavlink.MAV_DATA_STREAM_ALL,
        10,  # 10 Hz
        1    # Start
    )
    
    start_time = time.time()
    last_print = 0
    
    while time.time() - start_time < duration:
        msg = mav.recv_match(blocking=True, timeout=1)
        if msg is None:
            continue
        
        msg_type = msg.get_type()
        
        if msg_type == 'SERVO_OUTPUT_RAW':
            now = time.time()
            if now - last_print > 0.2:
                last_print = now
                
                def bar(val):
                    pct = max(0, min(1, (val - 1000) / 1000))
                    filled = int(pct * 15)
                    return '█' * filled + '░' * (15 - filled)
                
                m1 = msg.servo1_raw
                m2 = msg.servo2_raw
                m3 = msg.servo3_raw
                m4 = msg.servo4_raw
                
                print(f"\rM1:{m1:4d} {bar(m1)} | M2:{m2:4d} {bar(m2)} | M3:{m3:4d} {bar(m3)} | M4:{m4:4d} {bar(m4)}", end="", flush=True)
        
        elif msg_type == 'HEARTBEAT':
            armed = "ARMED" if msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED else "DISARMED"
            mode = mavutil.mode_string_v10(msg)
            print(f"\n[{armed}] Mode: {mode}")

if __name__ == '__main__':
    mav = connect_pixhawk()
    if mav:
        try:
            monitor_motors(mav, 120)
        except KeyboardInterrupt:
            print("\nGestopt")
        finally:
            mav.close()