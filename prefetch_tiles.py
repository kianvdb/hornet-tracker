#!/usr/bin/env python3
"""
prefetch_tiles.py — bulk-download tile-cache voor offline veldgebruik.

Gebruikt de Flask tile-route (/tiles/<source>/<z>/<x>/<y>.png) zodat alle
caching-logica op één plek blijft (de server). Dit script is een dunne
HTTP-client die de tiles berekent voor een gegeven gebied en ze één
voor één opvraagt.

Gebruik:
    python3 prefetch_tiles.py \\
        --lat 50.7686 --lon 4.2700 \\
        --radius 5 \\
        --zoom-min 13 --zoom-max 17 \\
        --sources osm,sat

Vóór gebruik: zorg dat de hornet-tracker service draait en internet werkt.
Tijdens prefetch wordt elke tile via Flask van OSM/ArcGIS opgehaald,
gecached, en geserveerd — dezelfde flow als wanneer de browser het zou
doen. Na prefetch zijn de tiles offline beschikbaar.

Throttling: max 4 parallelle requests, met kleine vertraging tussen
batches. OSM heeft een tile-usage policy die ~2 requests/sec per IP
toestaat. We blijven daar onder.
"""

import argparse
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request
import urllib.error


# Standaard server-URL — Flask draait lokaal op port 5000
SERVER_URL = 'http://localhost:5000'

# Throttling
MAX_PARALLEL = 4
BATCH_DELAY_SEC = 0.1
TIMEOUT_SEC = 10


def deg2num(lat_deg, lon_deg, zoom):
    """
    Converteer lat/lon (graden) naar tile-coordinaten (x, y) op een
    gegeven zoom-level. Standaard slippy-map (Mercator) projection.

    Op zoom Z is de wereld een grid van 2^Z × 2^Z tiles.
    """
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int(
        (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    )
    return xtile, ytile


def radius_to_bbox(lat, lon, radius_km):
    """
    Converteer (lat, lon, radius_km) naar een bounding box (lat_min,
    lat_max, lon_min, lon_max). Approximatie: 1 graad lat = 111 km.
    Voor lon hangt het af van de breedtegraad (kleiner naar de polen).
    """
    lat_offset = radius_km / 111.0
    lon_offset = radius_km / (111.0 * math.cos(math.radians(lat)))
    return (
        lat - lat_offset, lat + lat_offset,
        lon - lon_offset, lon + lon_offset,
    )


def tiles_for_bbox(lat_min, lat_max, lon_min, lon_max, zoom):
    """
    Bereken alle (x, y) tile-coordinaten die de bounding box dekken op
    een gegeven zoom. Returns lijst van (x, y) tuples.
    """
    # Belangrijk: y is geïnverteerd in slippy-map (noord = 0)
    # Dus lat_max → ymin, lat_min → ymax
    x_min, y_max = deg2num(lat_min, lon_min, zoom)
    x_max, y_min = deg2num(lat_max, lon_max, zoom)

    tiles = []
    for x in range(x_min, x_max + 1):
        for y in range(y_min, y_max + 1):
            tiles.append((x, y))
    return tiles


def fetch_tile(source, z, x, y):
    """
    Vraag één tile op via de Flask-route. Returns (success: bool,
    size_bytes: int, error_msg: str|None).

    De server-route doet zelf de cache + download logic, wij zijn alleen
    de trigger.
    """
    url = f"{SERVER_URL}/tiles/{source}/{z}/{x}/{y}.png"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SEC) as response:
            data = response.read()
            return True, len(data), None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return False, 0, "tile niet beschikbaar (404)"
        return False, 0, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return False, 0, f"netwerk: {e.reason}"
    except Exception as e:
        return False, 0, f"fout: {e}"


def prefetch_zoom_level(source, zoom, tiles, label):
    """
    Download alle tiles voor één (source, zoom) combinatie parallel.
    Toont progress in de terminal en returns (success_count, fail_count,
    bytes_downloaded).
    """
    total = len(tiles)
    success = 0
    fail = 0
    bytes_dl = 0

    print(f"\n  [{label}] {total} tiles te downloaden...")

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as executor:
        # Submit alle requests, krijg futures terug
        futures = {
            executor.submit(fetch_tile, source, zoom, x, y): (x, y)
            for (x, y) in tiles
        }

        done = 0
        for future in as_completed(futures):
            x, y = futures[future]
            ok, size, err = future.result()
            done += 1

            if ok:
                success += 1
                bytes_dl += size
            else:
                fail += 1

            # Update progress: simpele percentage, geen ANSI-tricks zodat
            # output bruikbaar blijft als script via tee/log loopt
            if done % 10 == 0 or done == total:
                pct = int(100 * done / total)
                print(
                    f"    {done}/{total} ({pct}%) — "
                    f"{success} OK, {fail} fail, "
                    f"{bytes_dl/1024:.0f} KB",
                    end='\r'
                )

        print()  # nieuwe regel na progress-bar

    if fail > 0:
        print(f"    !! {fail} tiles faalden — netwerk traag? OSM rate-limit?")

    return success, fail, bytes_dl


def main():
    global SERVER_URL

    parser = argparse.ArgumentParser(
        description='Prefetch map tiles voor offline veldgebruik',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Voorbeelden:
  # Klein gebied rond Inkendaal, hoge zoom (stadsdeel detail)
  %(prog)s --lat 50.7686 --lon 4.2700 --radius 2 --zoom-min 14 --zoom-max 17

  # Groter gebied, lagere max-zoom (sneller, minder detail)
  %(prog)s --lat 50.7686 --lon 4.2700 --radius 10 --zoom-min 11 --zoom-max 15

  # Alleen satelliet, geen stratenplan
  %(prog)s --lat 50.85 --lon 4.35 --radius 3 \\
           --zoom-min 13 --zoom-max 16 --sources sat
"""
    )
    parser.add_argument('--lat', type=float, required=True,
                        help='Latitude van centerpunt (graden)')
    parser.add_argument('--lon', type=float, required=True,
                        help='Longitude van centerpunt (graden)')
    parser.add_argument('--radius', type=float, required=True,
                        help='Radius rond centerpunt in km')
    parser.add_argument('--zoom-min', type=int, default=13,
                        help='Minimale zoom-level (default 13)')
    parser.add_argument('--zoom-max', type=int, default=17,
                        help='Maximale zoom-level (default 17)')
    parser.add_argument('--sources', type=str, default='osm,sat',
                        help='Comma-separated sources (default "osm,sat")')
    parser.add_argument('--server', type=str, default=SERVER_URL,
                        help=f'Flask server URL (default {SERVER_URL})')
    parser.add_argument('--dry-run', action='store_true',
                        help='Toon alleen schatting, download niet')
    args = parser.parse_args()

    # Validatie
    if args.zoom_min < 0 or args.zoom_max > 19 or args.zoom_min > args.zoom_max:
        print("Fout: zoom-min en -max moeten 0-19 zijn en min <= max")
        sys.exit(1)



    sources = [s.strip() for s in args.sources.split(',') if s.strip()]
    if not sources:
        print("Fout: geen sources gespecifieerd")
        sys.exit(1)

    SERVER_URL = args.server.rstrip('/')

    # Bereken bounding box + tile-aantallen vooraf
    lat_min, lat_max, lon_min, lon_max = radius_to_bbox(
        args.lat, args.lon, args.radius
    )

    print(f"Prefetch tiles")
    print(f"  Centrum:  {args.lat:.4f}, {args.lon:.4f}")
    print(f"  Radius:   {args.radius} km")
    print(f"  Bbox:     N {lat_max:.4f} → S {lat_min:.4f}, "
          f"W {lon_min:.4f} → E {lon_max:.4f}")
    print(f"  Zoom:     {args.zoom_min} t/m {args.zoom_max}")
    print(f"  Sources:  {', '.join(sources)}")
    print(f"  Server:   {SERVER_URL}")

    # Totaal-schatting
    total_tiles = 0
    per_zoom = {}
    for z in range(args.zoom_min, args.zoom_max + 1):
        tiles = tiles_for_bbox(lat_min, lat_max, lon_min, lon_max, z)
        per_zoom[z] = len(tiles)
        total_tiles += len(tiles) * len(sources)

    print(f"\n  Tiles per zoom (per source):")
    for z, count in per_zoom.items():
        print(f"    zoom {z}: {count} tiles")
    print(f"  Totaal: {total_tiles} tiles "
          f"(geschat {total_tiles * 0.025:.1f} MB)")

    if args.dry_run:
        print("\nDry-run klaar. Geen tiles gedownload.")
        return

    if total_tiles > 10000:
        print(
            f"\nWaarschuwing: {total_tiles} tiles is veel — "
            f"kan 10-30+ minuten duren."
        )
        answer = input("Doorgaan? [y/N] ")
        if answer.lower() != 'y':
            print("Geannuleerd.")
            return

    # Echte download
    start_time = time.time()
    grand_success = 0
    grand_fail = 0
    grand_bytes = 0

    for source in sources:
        print(f"\n=== Source: {source} ===")
        for z in range(args.zoom_min, args.zoom_max + 1):
            tiles = tiles_for_bbox(lat_min, lat_max, lon_min, lon_max, z)
            label = f"{source} zoom {z}"
            s, f, b = prefetch_zoom_level(source, z, tiles, label)
            grand_success += s
            grand_fail += f
            grand_bytes += b
            time.sleep(BATCH_DELAY_SEC)

    elapsed = time.time() - start_time
    print(f"\n{'=' * 50}")
    print(f"Klaar in {elapsed:.0f} seconden")
    print(f"  Gelukt: {grand_success} tiles")
    print(f"  Mislukt: {grand_fail} tiles")
    print(f"  Totaal gedownload: {grand_bytes/1024/1024:.1f} MB")
    print(f"{'=' * 50}")


if __name__ == '__main__':
    main()
