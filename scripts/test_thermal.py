#!/usr/bin/env python3
"""
test_thermal.py — standalone hardware-test voor MLX90640

Doel: bevestigen dat de sensor uitleesbaar is vanuit Python voordat we
gaan integreren in app.py. Leest 10 frames, print min/max/avg temperatuur
per frame, en slaat het laatste frame op als PNG.

Library: adafruit-circuitpython-mlx90640
Bus: /dev/i2c-3 (software-emulated I2C via dtoverlay, vereist voor
clock-stretching support — hardware I2C op bus 1 ondersteunt dit niet
betrouwbaar op de BCM2835).

Pinout:
    SDA = GPIO 23 (header pin 16)
    SCL = GPIO 24 (header pin 18)
    VIN = 3.3V    (header pin 1)
    GND =         (header pin 6)

Run:
    cd ~/hornet-tracker
    python3 scripts/test_thermal.py
"""

import time
import sys
import numpy as np
from adafruit_extended_bus import ExtendedI2C
import adafruit_mlx90640

REFRESH_RATE_HZ = 1   # software I2C is traag, start conservatief

REFRESH_MAP = {
    1:  adafruit_mlx90640.RefreshRate.REFRESH_1_HZ,
    2:  adafruit_mlx90640.RefreshRate.REFRESH_2_HZ,
    4:  adafruit_mlx90640.RefreshRate.REFRESH_4_HZ,
    8:  adafruit_mlx90640.RefreshRate.REFRESH_8_HZ,
    16: adafruit_mlx90640.RefreshRate.REFRESH_16_HZ,
    32: adafruit_mlx90640.RefreshRate.REFRESH_32_HZ,
    64: adafruit_mlx90640.RefreshRate.REFRESH_64_HZ,
}

print("I2C bus 3 (software I2C) initialiseren...")
i2c = ExtendedI2C(1)   # /dev/i2c-3

print("MLX90640 initialiseren...")
mlx = adafruit_mlx90640.MLX90640(i2c)
print(f"  Serienummer: {[hex(i) for i in mlx.serial_number]}")

mlx.refresh_rate = REFRESH_MAP[REFRESH_RATE_HZ]
print(f"  Refresh rate: {REFRESH_RATE_HZ} Hz")

print(f"\nLees 10 frames...\n")

frame = np.zeros(768, dtype=np.float32)
last_frame_2d = None

for i in range(10):
    t0 = time.time()
    try:
        mlx.getFrame(frame)
    except ValueError as e:
        print(f"Frame {i+1}: skipped ({e})")
        continue
    except Exception as e:
        print(f"Frame {i+1}: FOUT: {e}")
        continue

    dt = (time.time() - t0) * 1000
    arr = frame.reshape(24, 32)
    print(f"Frame {i+1:2d}  "
          f"min={arr.min():5.1f}°C  "
          f"max={arr.max():5.1f}°C  "
          f"avg={arr.mean():5.1f}°C  "
          f"({dt:.0f}ms)")
    last_frame_2d = arr.copy()

if last_frame_2d is not None:
    try:
        from PIL import Image
        normalized = ((last_frame_2d - last_frame_2d.min()) /
                      (last_frame_2d.max() - last_frame_2d.min() + 1e-6) * 255)
        img = Image.fromarray(normalized.astype(np.uint8), mode='L')
        img = img.resize((320, 240), Image.BILINEAR)
        img.save('/tmp/thermal_test.png')
        print(f"\nLaatste frame opgeslagen: /tmp/thermal_test.png")
    except ImportError:
        print("\n(PIL niet geinstalleerd: pip3 install Pillow --break-system-packages)")
else:
    print("\nGeen frames gelezen, geen PNG geschreven")

print("\nKlaar.")
