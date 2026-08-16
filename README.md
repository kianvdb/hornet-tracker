# 🐝 Hornet Tracker — VespaTrack Ground Station

Real-time grondstation-dashboard voor het lokaliseren van Aziatische
hoornaarnesten met een autonome RF-tracking drone. Bachelorthesis-project
aan Erasmushogeschool Brussel, opleiding Multimedia en Creatieve
Technologie.

De drone — VespaTrack — draagt een RF-ontvanger en zoekt zelfstandig
naar een kleine LoRa-beacon die op een testobject is geplakt. Eén knop
op het dashboard start een volledige zoekvlucht: de drone peilt de
richting van de beacon, controleert die richting, vliegt er dwars
overheen en bepaalt uit twee kruisingen de positie. Het grondstation
toont ondertussen in real-time de drone-positie op een satellietkaart,
het ontvangen LoRa-signaal met RSSI en signaalgetrouwheid per packet,
een thermisch beeld voor visuele bevestiging op korte afstand, en biedt
bediening van de vlucht.

> **Status.** Het zoekalgoritme is af, gevlogen en drie keer herzien op
> grond van veldmetingen. Het toestel zelf niet: na een val in augustus
> 2026 vertoont het een mechanisch draaikoppel dat na vijf inspecties
> niet gelokaliseerd is, en de toestandscontrole weigert sindsdien elke
> vlucht. Het platform wordt herbouwd met behoud van de volledige
> radioketen. Zie [`docs/zoekalgoritme-ontwerp.md`](docs/zoekalgoritme-ontwerp.md) §8.

---

## Inhoudsopgave

- [Architectuur](#architectuur)
- [Hardware](#hardware)
- [Software stack](#software-stack)
- [Installatie](#installatie)
- [Service beheer](#service-beheer)
- [Projectstructuur](#projectstructuur)
- [Netwerktoegang](#netwerktoegang)
- [Offline veldwerk](#offline-veldwerk)
- [Thermisch beeld](#thermisch-beeld)
- [LoRa signaal](#lora-signaal)
- [Zoekalgoritme](#zoekalgoritme)
- [Adres-lookup](#adres-lookup)
- [Ontwikkelen](#ontwikkelen)
- [Roadmap](#roadmap)
- [Licentie](#licentie)
- [Auteur](#auteur)
- [Bronvermelding](#bronvermelding)

---

## Architectuur

```text
┌─────────────┐    LoRa 433 MHz    ┌─────────────────────────┐
│  Beacon op  │ ─ ─ ─ ─ ─ ─ ─ ─ ─> │      Drone-payload      │
│  testobject │                    │  - Pixhawk 6C Mini      │
└─────────────┘                    │  - Raspberry Pi 4       │
                                   │  - SX1278 Ra-01 (LoRa)  │
                                   │  - MLX90640 (warmtecam) │
                                   └───────────┬─────────────┘
                                               │ WiFi hotspot
                                               │ of ethernet
                                               ▼
                                   ┌─────────────────────────┐
                                   │  Operator veldlaptop    │
                                   │  Browser → dashboard    │
                                   └─────────────────────────┘
```

De Raspberry Pi op de drone draait een Flask + Socket.io webserver. De
operator opent het dashboard in een browser op de veldlaptop. Alle
communicatie tussen drone en grondstation gebeurt via Socket.io
websockets zodat de UI in real-time updates ontvangt zonder polling.
Commando's vanuit het dashboard (arm, disarm, mode-wijziging, en het
starten van een zoekmissie) gaan dezelfde weg terug naar de Pi en
worden via MAVLink doorgestuurd naar de Pixhawk.

De autonome vluchtlogica draait in eigen modules op de Pi
(`search.py`, `pattern.py`, `approach.py`, `mission.py`), elk in een
aparte thread die de gedeelde status-dict leest voor telemetrie. De
zender blijft daarbij altijd primair: zodra de vluchtmodus niet langer
GUIDED is, stopt het script onmiddellijk met commando's geven, schrijft
het weg wat er gemeten is en sluit het af.

---

## Hardware

### Drone-platform

- **Tarot 650 Sport frame** — drone airframe
- **Pixhawk 6C Mini + ArduCopter 4.6.3** — flight controller
- **Raspberry Pi 4** — companion computer (dashboard + tracking-logica)

### RF-tracking

- **SX1278 Ra-01 LoRa-ontvanger** via SPI met Yagi 433 MHz antenne
  (~10 dBi gain) verbonden via IPEX-SMA pigtail. Decodeert `HT,<id>,<count>`
  packets van een Arduino Pro Mini beacon (zelfde SX1278 chip). Alle
  veldmetingen zijn gedaan op **2 dBm** TX-power; de module kan tot
  20 dBm maar dat is in het veld nooit gebruikt.

- Tijdens ontwikkeling is ook een RTL-SDR USB-stick gebruikt voor
  energy-detection als baseline-systeem (zie SignalSource interface
  in `app.py`).

> **Openstaand: 45 dB link-tekort.** Over drie gemeten afstanden is de
> ontvangen signaalsterkte constant ~45 dB zwakker dan vrije-ruimte-
> propagatie voorspelt. Een tekort dat níét met de afstand verandert
> wijst op een vaste verliespost in de keten — kruispolarisatie of een
> niet-resonante antenne. Dit is vermoedelijk ook de reden dat de
> antennelob 150-212° breed is en de peiling daardoor op ±30° blijft
> steken. Een polarisatietest van twee minuten staat nog open en is de
> goedkoopste ingreep met de grootste opbrengst.

### Sensoren

- **Pimoroni MLX90640 55°** — thermische camera (32×24 pixels) op I2C bus 1.
  Default-adres `0x33`. I2C bus draait op 400 kHz
  (`dtparam=i2c_arm_baudrate=400000` in `/boot/firmware/config.txt`).
  Refresh-rate `REFRESH_8_HZ` (effectief ~4 FPS over de bus) — geeft
  stabiele frames zonder I2C glitches. De camera levert visuele
  bevestiging van een nest op korte afstand.

### Communicatie

- **Directe USB (CDC-ACM)** — Pi ↔ Pixhawk verbinding voor MAVLink,
  via de USB-C poort van de Pixhawk 6C. Bidirectioneel (telemetrie
  ontvangen én commando's sturen) en betrouwbaar. De Pi spreekt de
  poort aan via het stabiele `/dev/serial/by-id/`-pad zodat het
  device-nummer niet kan wisselen tussen boots.
- **Comfast MT7612U** — USB WiFi-adapter op de Pi, host van veldhotspot

> **Historisch**: eerdere versies gebruikten een USB-TTL CH340-adapter
> naar TELEM2 en een SiK-radio als backup-telemetrie. Beide zijn
> vervangen door de directe USB-verbinding, die bidirectioneel
> betrouwbaar bleek waar de TTL-route pakketten verloor.


---

## Software stack

- **Backend** — Python 3, Flask, Flask-SocketIO met threading async mode,
  threading-based background loops voor signal/wifi/mavlink/thermal
- **MAVLink** — pymavlink, bidirectioneel: telemetrie ontvangen
  (GPS, batterij, mode, armed) én commando's sturen (arm/disarm/mode)
- **LoRa packet decoding** — pyLoRa library (SX127x) via SPI met
  polling-based RX in een aparte thread. Interrupt-callbacks worden
  niet ondersteund door `rpi-lgpio` (de drop-in vervanger van `RPi.GPIO`
  op Pi OS Bookworm), dus we pollen het IRQ-register elke 50 ms — ruim
  fijn genoeg voor de 2 Hz beacon zonder noemenswaardige CPU-impact.
- **RF-detectie (legacy/optioneel)** — pyrtlsdr met FFT-based energy
  detection en peak-hold baseline. Pad blijft beschikbaar via
  `SIGNAL_SOURCE = 'rtlsdr'` in `app.py`.
- **Thermische camera** — Adafruit CircuitPython MLX90640 library via
  I2C, frame-stream via aparte Socket.io `thermal_frame` event om de
  status-payload klein te houden
- **Excel-export** — openpyxl voor server-side generatie van
  gestileerde XLSX-bestanden uit log-entries
- **Frontend** — vanilla JavaScript (geen build-step), Leaflet voor de
  kaart (lokaal gehost), Canvas voor thermische rendering, fetch-API
  voor REST-calls naar de backend
- **Offline tiles** — Flask-route serveert lokaal gecachte map-tiles
  uit `data/tiles/`, met internet-fallback bij cache-miss
- **Adres-geocoding** — Nominatim API (OpenStreetMap) voor zowel
  forward geocoding (adres → coördinaten, voor zoekvelden) als reverse
  geocoding (coördinaten → adres, voor log-entries en Excel-export).
  Gedeelde utility-module `nominatim.js` voor consistente debounce en
  rate-limit-handling tussen de meerdere consumers in het dashboard.

Het dashboard draait als achtergrondproces op de Raspberry Pi via een
systemd service (`hornet-tracker.service`). Zie [Installatie](#installatie)
voor de setup.

---

## Installatie

### 1. Project clone

```bash
git clone https://github.com/kianvdb/hornet-tracker.git
cd hornet-tracker
```

### 2. Python dependencies installeren

```bash
sudo apt update
sudo apt install python3-pip git i2c-tools
pip3 install -r requirements.txt
pip3 install adafruit-circuitpython-mlx90640 --break-system-packages
pip3 install pyLoRa --break-system-packages
```

Op Pi OS Bookworm werkt `RPi.GPIO` niet meer correct met `add_event_detect`
omdat de onderliggende GPIO-driver van `/dev/gpiomem` naar `/dev/gpiochipN`
is gemigreerd. Vervang door drop-in compatibele `rpi-lgpio`:

```bash
pip3 uninstall RPi.GPIO -y --break-system-packages
pip3 install rpi-lgpio --break-system-packages
```

Het bestand `requirements.txt` ligt in de root van het project en bevat:

```text
flask
flask-socketio
pymavlink
pyrtlsdr
numpy
openpyxl
```

### 3. I2C activeren voor thermische camera

I2C is op de Pi standaard uitgeschakeld. Activeren via raspi-config of
direct in `/boot/firmware/config.txt`:

```bash
sudo nano /boot/firmware/config.txt
# Zoek/voeg toe:
dtparam=i2c_arm=on,i2c_arm_baudrate=400000

sudo reboot
```

Verifieer dat de MLX90640 op de bus zichtbaar is:

```bash
sudo i2cdetect -y 1
# Verwacht: '33' in het 16x16 raster
```

### 4. SPI activeren voor LoRa-ontvanger

SPI is op de Pi standaard uitgeschakeld. Activeren in `/boot/firmware/config.txt`:

```bash
sudo nano /boot/firmware/config.txt
# Zoek/voeg toe:
dtparam=spi=on

sudo reboot
```

Verifieer dat de SPI-devices verschijnen:

```bash
ls /dev/spidev*
# Verwacht: /dev/spidev0.0 en /dev/spidev0.1
```

Daarna kan de Ra-01 ontvanger getest worden met de smoke test:

```bash
python3 /tmp/lora_chipid.py
# Verwacht: RegVersion = 0x12, Chip detected: SX1276/77/78/79
```

### 5. Systemd service registreren

`systemd` is het service-management systeem van Linux dat ervoor zorgt
dat het dashboard automatisch start bij boot en herstart na een crash.

Maak het unit-bestand `/etc/systemd/system/hornet-tracker.service`:

```ini
[Unit]
Description=Hornet Tracker Dashboard
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/hornet-tracker
ExecStart=/usr/bin/python3 /home/pi/hornet-tracker/app.py
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Activeren:

```bash
sudo systemctl daemon-reload
sudo systemctl enable hornet-tracker
sudo systemctl start hornet-tracker
```

Vanaf nu start het dashboard automatisch bij elke boot van de Pi.

### 6. Pixhawk USB-verbinding

De Pixhawk wordt via USB-C rechtstreeks op de Pi aangesloten en verschijnt
als `/dev/ttyACM0`. Omdat dat nummer tussen boots kan wisselen, gebruikt
`app.py` het stabiele by-id-pad dat altijd naar déze Pixhawk wijst:

```bash
ls -l /dev/serial/by-id/
# usb-Holybro_Pixhawk6C_<serienummer>-if00 -> ../../ttyACM0
```

Dat pad staat in `MAVLINK_DEVICE` bovenaan `app.py`. Een udev-regel is
niet meer nodig; het by-id-pad is inherent stabiel. De baudrate is een
formaliteit omdat CDC-ACM hem negeert.


### 7. Comfast hotspot configureren (optioneel, voor veldwerk)

Voor veldgebruik zonder router maakt de Pi een eigen WiFi-hotspot via
de Comfast MT7612U USB-adapter op `wlan1`. NetworkManager regelt dit:

```bash
sudo nmcli device wifi hotspot \
    ifname wlan1 \
    con-name HornetTracker \
    ssid HornetTracker \
    password "<wachtwoord>"

# Statisch IP op hotspot
sudo nmcli connection modify HornetTracker \
    ipv4.method shared \
    ipv4.addresses 192.168.4.1/24 \
    connection.autoconnect yes
```

Wlan0 (interne Pi WiFi) blijft beschikbaar voor SSH naar thuisnetwerk
tijdens ontwikkeling.

---

## Service beheer

```bash
sudo systemctl start hornet-tracker      # starten
sudo systemctl stop hornet-tracker       # stoppen
sudo systemctl restart hornet-tracker    # herstarten na code-wijziging
sudo systemctl status hornet-tracker     # status + recente logregels
sudo journalctl -u hornet-tracker -f     # live logs volgen
```

---

## Projectstructuur

```text
hornet-tracker/
├── app.py                          Flask + Socket.io backend, MAVLink,
│                                   RTL-SDR/LoRa SignalSource, WiFi status,
│                                   command handlers, thermal camera loop,
│                                   Excel-export endpoint, tile-cache routes,
│                                   Nominatim reverse-geocoding helpers
│
│   ── autonome vluchtmodules, elk in een eigen thread ──
├── mission.py                      basiscommando's: GUIDED, arm, takeoff,
│                                   hoogte-clamp. Fundament onder de rest
├── pattern.py                      360°-rotatiemeting: stralingsdiagram
│                                   opnemen (36 × 10°, stilstaand gemeten)
├── approach.py                     rechte nadering naar de bron, stapsgewijs
├── search.py                       het zoekalgoritme: hovertest + 5 fasen
│                                   (peilen, verifiëren, doorvliegen,
│                                   terugkruisen, afronden). Zie docs/
├── download_dataflash.py           .BIN-vluchtlogs van de Pixhawk halen
│                                   (wordt geïmporteerd door app.py)
├── prefetch_tiles.py               CLI tool: bulk-download tiles voor offline gebruik
├── README.md                       dit bestand
├── requirements.txt                Python dependencies
├── .gitignore
│
├── docs/
│   └── zoekalgoritme-ontwerp.md    zelfstandige spec van search.py: elke
│                                   parameter met zijn herkomst, het
│                                   accu-budget, de faalgevallen, en per
│                                   veldvlucht wat er is gewijzigd
│
├── scripts/                        losse operatorgereedschappen
│   ├── read_params.py              Pixhawk-parameters uitlezen
│   └── set_params.py               hoogte-parameters zetten
│
├── tests/                          draaien zonder drone, via de simulator
│   ├── README.md                   hoe je ze draait en wat ze dekken
│   ├── sim.py                      nagebouwde MAVLink + signaalbron
│   ├── test_eenheden.py            snelle eenheidstests (~1 s)
│   ├── test_vlucht.py              vier volledige vluchten in simulatie (~3 min)
│   └── uitvoer/                    testuitvoer (gitignored, raakt data/ nooit)
│
├── templates/
│   └── dashboard.html              pure markup, geen inline JS/CSS
│
├── data/                           meetdata + runtime state
│   ├── README.md                   uitleg over wat hier hoort
│   ├── pattern_*.csv               rotatiemetingen — ONDERZOEKSDATA, in git
│   ├── approach_*.csv              naderingsmeting — ONDERZOEKSDATA, in git
│   ├── search_*.csv                zoekvluchten — ONDERZOEKSDATA, in git
│   ├── coord-log.json              gelogde entries (met adres-cache), gitignored
│   └── tiles/                      lokaal gecachte map-tiles (gitignored)
│       ├── osm/<z>/<x>/<y>.png     OpenStreetMap stratenplan
│       ├── sat/<z>/<x>/<y>.png     ArcGIS satelliet (JPEG inhoud)
│       └── hyb/<z>/<x>/<y>.png     ArcGIS straatnamen overlay
│
└── static/
    ├── socket.io.min.js            client library (vendored)
    │
    ├── img/                        statische afbeeldingen
    │   ├── vespatrack-logo.svg     logo in navbar
    │   └── favicon.svg             browser-tab icoon (SVG, schaalbaar)
    │
    ├── vendor/leaflet/             Leaflet library lokaal gehost (offline-proof)
    │   ├── leaflet.js
    │   ├── leaflet.css
    │   └── images/                 marker icons, layers-control icons
    │
    ├── css/                        modulaire styling per component
    │   ├── base.css                reset, body, gedeelde kleuren, responsive
    │   ├── layout.css              2-koloms dashboard layout
    │   ├── navbar.css              vaste navbar bovenaan met status-popovers
    │   ├── cards.css               grid cards + status rows + signaalbalkjes
    │   ├── signal.css              LoRa signal card met 3-tier hierarchie
    │   ├── map.css                 Leaflet kaart + drone-marker + adres-zoek
    │   ├── coord-log.css           gelogde coördinaten + status badges + adres
    │   ├── controls.css            knoppen + modals + log/export/tile-cache modals
    │   └── thermal.css             warmtecamera canvas + stats + baseline-knoppen
    │                               + palette dropdown
    │
    └── js/                         modulaire JavaScript per concern
        ├── utils.js                rssi helpers, toast, card status
        ├── nominatim.js            gedeelde Nominatim utility: forward search
        │                           + autocomplete-flow voor input-velden
        ├── map.js                  Leaflet init, drone marker (SVG arrow met
        │                           heading-rotatie), afgelegde weg, click-handler,
        │                           adres-zoek in map card header
        ├── coord-log.js            log entries, map-click pin, status/notitie/
        │                           adres, edit, delete, REST naar backend,
        │                           Excel-export
        ├── drone-controls.js       arm/disarm/mode + command result handler
        ├── mission-controls.js     START MISSIE + zoekhoogte + noodknoppen,
        │                           voortgangsmeldingen per zoekfase
        ├── pattern-controls.js     bediening van de rotatiemeting (pattern.py)
        ├── tabs.js                 tabbladen Veld / Thermisch / Coördinaten —
        │                           uit gebruikerstest 1: alles op één scherm
        ├── signal-display.js       LoRa signal card 3-tier rendering: RSSI/badge/bar,
        │                           signaalgetrouwheid + packet-age, packets + tracker ID
        ├── thermal-display.js      MLX90640 canvas rendering + Iron/Inferno/
        │                           Grayscale/Rainbow paletten + baseline detectie
        ├── navbar.js               popover toggle + click-outside-to-close
        ├── socket-handlers.js      connect/disconnect/status_update dispatch
        ├── modals.js               shutdown/reboot/arm/log/export/tile-cache dialogen
        ├── tile-cache.js           cache stats + prefetch UI (gebruikt nominatim.js)
        └── main.js                 bootstrap (socket + init + log fetchen van Pi)
```

### Frontend module-volgorde

`dashboard.html` laadt de JS-modules in deze volgorde:

1. `utils.js`, `map.js`, `coord-log.js`, `drone-controls.js`,
   `mission-controls.js`, `signal-display.js`, `thermal-display.js` —
   definiëren functies op `window`, geen socket nodig.
2. `socket-handlers.js` — handlers die `window.socket` gebruiken.
3. `modals.js` — dialog-logica voor alle modals.
4. `nominatim.js`, `tile-cache.js` — gedeelde geocoding-utility en de
   offline tiles UI die hem consumeert.
5. `navbar.js`, `tabs.js`, `pattern-controls.js` — popovers, tabbladen
   en de bediening van de rotatiemeting.
6. `main.js` — bootstrap: maakt de socket aan, haalt log van de Pi via
   `GET /api/log`, initialiseert alles na `DOMContentLoaded`.

Alle module-functies worden op `window` gezet zodat inline
`onclick="..."` handlers in de HTML rechtstreeks werken zonder
build-step of bundler. Omdat elke module alleen definities uitvoert bij
het laden en het echte werk pas na `DOMContentLoaded` begint, is de
onderlinge volgorde binnen een groep niet kritisch.

---

## Netwerktoegang

Het dashboard is bereikbaar op poort 5000. Afhankelijk van de setup:

| URL                              | Context                                  |
| -------------------------------- | ---------------------------------------- |
| `http://hoornaar-tracker:5000`   | via mDNS (thuisnetwerk ethernet)         |
| `http://192.168.1.4:5000`        | direct IP op thuisnetwerk                |
| `http://192.168.4.1:5000`        | HornetTracker hotspot (veldwerk)         |

De Pi draait optioneel een eigen WiFi-hotspot op `wlan1` (Comfast
MT7612U) zodat de veldlaptop in het veld zonder router verbinding kan
maken.

> **Excel-downloads via HTTP**: moderne browsers markeren xlsx-downloads
> van HTTP-bronnen als "onveilig" en blokkeren ze met een
> bevestigingsdialoog. Operator klikt eenmalig "Behouden" in de
> download-balk. Self-signed HTTPS-certificaten zijn geprobeerd maar
> bleken in de praktijk omslachtiger (cert-waarschuwing per device, per
> URL, met onbetrouwbare click-through) dan de huidige "Behouden"-klik.
> Voor productiegebruik door een bestrijders-instantie zou een echt
> TLS-certificaat van een publieke CA nodig zijn op een publiek domein,
> buiten scope van deze thesis.

---

## Offline veldwerk

Voor echt offline-gebruik tijdens veldwerk (geen WiFi-uplink, geen
4G-bereik) moet de Pi alle map-tiles **vooraf** downloaden naar de
lokale cache. Tijdens normaal gebruik vult de cache zich ook
automatisch met tiles die de operator daadwerkelijk bekijkt — maar
voor missie-kritisch veldwerk is gerichte prefetch nodig.

### Tile-bronnen

Drie kaartlagen zijn beschikbaar:

| Source | Beschrijving | Formaat |
|--------|--------------|---------|
| `osm`  | OpenStreetMap stratenplan | PNG |
| `sat`  | ArcGIS satellietbeeld | JPEG (geserveerd als .png-URL) |
| `hyb`  | ArcGIS straatnamen overlay (transparant) | PNG |

Tiles worden opgeslagen onder `data/tiles/<source>/<z>/<x>/<y>.png` in
slippy-map / Mercator-projectie (standaard voor alle web-maps).

### Prefetch via dashboard

In de navbar staat een 🗺️-knop die de **offline kaart-tiles modal**
opent. Hierin:

1. **Stats** per source: aantal tiles + MB op disk
2. **Adres-zoek** via Nominatim (OpenStreetMap geocoder): typ
   "Inkendaal Vlezenbeek" of "Brussel" en selecteer een suggestie
3. **Of**: knop "📍 Gebruik huidige drone-positie" als er een GPS-fix is
4. **Radius** in km + **zoom-range** (typisch 13-16 voor wijk + gebouw detail)
5. **Bron-selectie** via vinkjes
6. **"Download starten"** start een achtergrond-prefetch met live
   progress-bar

Cache-stats updaten automatisch na voltooiing. Operator kan per source
of in zijn geheel wissen.

### Prefetch via CLI

Voor scripted gebruik of grotere downloads is er ook een command-line
tool:

```bash
python3 prefetch_tiles.py \
    --lat 50.7686 --lon 4.2700 \
    --radius 5 \
    --zoom-min 13 --zoom-max 17 \
    --sources osm,sat,hyb
```

Met `--dry-run` toont het script alleen hoeveel tiles + MB het gaat
downloaden, zonder daadwerkelijk te downloaden. Handig om te zien of
een gebied + zoom-range realistisch is.

### Schatting per radius

| Radius | Zoom 13-17, alle 3 sources | Geschatte grootte |
|--------|----------------------------|-------------------|
| 1 km   | ~50 tiles | ~2 MB |
| 2 km   | ~330 tiles | ~10 MB |
| 5 km   | ~2000 tiles | ~60 MB |
| 10 km  | ~7800 tiles | ~250 MB |

### Internet-fallback

Bij cache-miss probeert Flask de tile alsnog van internet te halen en
op te slaan. Bij geen internet: 404, Leaflet toont een grijs vlak.
Browse-gebruik vult de cache organisch met tiles die de operator
daadwerkelijk bezoekt, naast gerichte prefetch.

---

## Thermisch beeld

Het dashboard toont het live thermisch beeld van de MLX90640 sensor
op de drone. Het beeld is 32×24 pixels native, upscaled naar 320×240
op het canvas met nearest-neighbor scaling (geen blur, scherpe pixels).
Onder het beeld staan vier waarden: Min / Gem / Max temperatuur in °C
en de actuele FPS.

### Render-modi

Twee complementaire modi waar de operator tussen kan wisselen:

**Normaal** (default) — hybride scaling.

Bij voldoende temperatuur-spreiding in de scène (≥ 15°C tussen koudste
en warmste pixel) auto-scalen we het palette op die range — maximaal
contrast. Bij minder spreiding (uniforme scène zoals lege weide of
plafond) gebruiken we een vaste range van 15°C gecentreerd op het
gemiddelde, zodat sensor-ruis niet als nep-warmte-vlekken verschijnt.
Geschikt voor algemene observatie.

**Detectie** — verschil tov baseline.

Operator drukt op "Baseline instellen" om de huidige scène als
referentie op te slaan. Vanaf dan toont het beeld alleen pixels die
boven baseline liggen — pixels op of onder baseline worden zwart.
Geschikt voor gerichte hot-spot zoektocht: drone vliegt over een
gebied, baseline is de "normale" omgeving, en alleen nieuwe warmere
objecten (een nest, een dier) lichten op tegen zwarte achtergrond.

### Palette-keuze

Vier paletten beschikbaar via dropdown rechts in de card-header:

- **🔥 Iron** (default) — klassieke thermal cam look, paars → rood →
  geel → wit
- **🌋 Inferno** — matplotlib wetenschappelijk standaard, zwart →
  paars → rood → geel
- **⬜ Grijswaarden** — geprint/thesis-rapport vriendelijk, zwart → wit
- **🌈 Rainbow** — oude FLIR-stijl, blauw → groen → geel → rood

Switchen heeft geen reload nodig — het volgende frame (binnen ~150ms)
gebruikt automatisch de nieuwe palette.

### Performance

I2C-bus op 400 kHz, sensor refresh-rate op 8 Hz (config in `app.py`).
Backend leest ~4 frames per seconde succesvol; bij hogere instellingen
treden I2C-glitches op die de stream onderbreken. Bij 50+ skipped
frames detecteert de loop dat en herverbindt automatisch met de
sensor — geen handmatig ingrijpen nodig.

Frames worden gerate-limit naar de browser (max 6-7 emits/sec) en
gerond op 1 decimaal, zodat de JSON-payload ~5 KB per frame blijft.

---

## LoRa signaal

Het dashboard toont in real-time het signaal van de LoRa-beacon op het
testobject. Anders dan bij energy-detection met RTL-SDR (waar we een
ruis-baseline meten en kijken hoeveel dB het signaal daarboven uitkomt)
geeft de Ra-01 ontvanger ons **echte RSSI per gedecodeerd packet** in
dBm. Dat is een meer betekenisvolle waarde voor link-budget en bereik
analyse.

### Beacon-protocol

De beacon is een Arduino Pro Mini 3.3V/8MHz met SX1278 Ra-01 module,
zelfde chip als de ontvanger. Beacon-code in `beacon.ino`:

- **Frequentie**: 433 MHz
- **Bandwidth**: 125 kHz
- **Spreading factor**: 7 (snelle modulation, korte time-on-air)
- **Coding rate**: 4/5
- **CRC**: aan
- **Sync word**: default 0x12

Beacon stuurt elke 500 ms (2 Hz) een ASCII-payload `HT,<id>,<count>`:

- `HT` — protocol-identifier (Hornet Tracker)
- `<id>` — tracker-ID, voorzien voor multi-tracker uitbreiding
- `<count>` — TX-counter sinds beacon-power-on

TX-power **5 dBm** voor USB-TTL testing (CH340 regulator levert
~50 mA, SX1278 piek tijdens TX is 120 mA — brownout-reset zonder
verlaging).

Alle veldmetingen in `data/` zijn gedaan op **2 dBm**, niet op de
eerder voorziene 20 dBm — dat staat ook in de kop van elk meetbestand.
Een range-test op vol vermogen staat nog open; de huidige cijfers over
bereik en peilnauwkeurigheid gelden dus voor 2 dBm.

Zendinterval is 2 Hz. Dat is bewust vlak onder het maximum: bij een
time-on-air van ~41 ms en 10% duty cycle op de 433 MHz ISM-band ligt
de bovengrens rond 2,4 Hz.

### Dashboard layout

Drie-tier hiërarchie in de signal-card:

**Tier 1 — primaire info**:
- Grote RSSI-waarde in dBm (groen, monospace)
- Detectie-badge: "● Signaal" (groen, pulse) of "Geen signaal" (grijs)
- Absolute RSSI-bar met gradient rood (-120) → oranje (-80) → groen (-40)

**Tier 2 — link-kwaliteit metrics**:
- **Signaalgetrouwheid** (in het dashboard zo genoemd; technisch de
  SNR of signal-to-noise ratio) in dB. LoRa kan tot -20 dB decoderen —
  een signaal 100× zwakker dan de ruis, wat voor andere
  modulatie-schema's onmogelijk is. Positief betekent een gezonde
  link, negatief betekent marginaal en packets kunnen wegvallen. De
  operator gebruikt dit als vroege waarschuwing.

  De term SNR is na de eerste gebruikerstest vervangen: geen van beide
  verdelgers kon hem verklaren. Zie de gebruikerstesten in de
  Roadmap.
- **Laatste packet**: tijd sinds laatste succesvol ontvangen packet.
  Bij stilte > 3 seconden zakt RSSI naar de silence-floor van
  -120 dBm; teller blijft oplopen tot er weer een packet binnenkomt.

**Tier 3 — administratief**:
- **Packets**: totale teller sinds service-start. Reset bij Pi-reboot
  of service-restart, telt door over operator-acties zoals
  page-refresh of beacon-uit-en-aan.
- **Tracker ID**: het ID uit de laatste packet. Toont "ID --" tot
  eerste packet binnenkomt. Voorzien voor multi-tracker uitbreiding
  (zie [Roadmap](#roadmap)).

### Detectie-criterium

`signal_detected = packet binnen 3 seconden ontvangen`. Anders dan bij
RTL-SDR is er geen baseline-threshold meer — packet-decodering is
binary (CRC OK of niet), dus "signaal aanwezig" is letterlijk "we
hebben recent een geldig packet gekregen".

Bij verlies van signaal:
- 0–3 sec sinds laatste packet: badge blijft groen, RSSI blijft op
  laatste waarde
- > 3 sec: badge wordt grijs, RSSI zakt naar -120 dBm, bar leegt
- SNR + Tracker ID blijven op laatste waarde staan (info uit laatste
  packet, geen reden om te wissen)

### Architectuur op de Pi

`SignalSource` is een abstracte interface in `app.py` met twee
implementaties:

- **`RtlSdrSource`** — energy-detection via FFT op RTL-SDR samples
  met peak-hold over 400 ms en dynamische baseline
- **`LoRaSource`** — packet-decoding via pyLoRa op SPI

`SIGNAL_SOURCE = 'lora'` in de config bovenaan `app.py` activeert
LoRa-mode. De `signal_loop` heeft een apart pad voor LoRa zonder
peak-hold of baseline-meting; bij RTL-SDR-mode blijft de bestaande
flow ongewijzigd. Voor onboard demo's of fallback testing kan
gewisseld worden zonder andere code aan te raken.

**Polling in plaats van interrupts**: pyLoRa probeert standaard
DIO0-interrupts te gebruiken voor RxDone events, maar `add_event_detect`
in `rpi-lgpio` (de drop-in vervanger van `RPi.GPIO` op Pi OS Bookworm)
werkt niet betrouwbaar voor SPI-IRQ patronen. Daarom polleert
`LoRaSource._rx_loop` elke 50 ms het `RegIrqFlags` register direct.
Bij de 2 Hz beacon-rate is dat ruim genoeg — tien pollingrondes per
verwacht packet — en het kost geen merkbare CPU-tijd.

> Dit is ook waarom het waargenomen pakketverlies (1,5-1,9 Hz in plaats
> van 2 Hz tijdens de doorvluchten) niet aan de ontvangstlus kan liggen:
> die kan een 2 Hz-beacon simpelweg niet missen. Het verlies zit op
> radioniveau of bij de zender. Zie [Roadmap](#roadmap) — CRC-fouten
> tellen is de eerste stap om dat uit elkaar te halen.

### Hardware-quirk: bedrading-stabiliteit

De Ra-01 module heeft tijdens dit project drie keer een soldeer/contact
issue opgeleverd waarbij `RegVersion` als `0x00` werd gelezen (MISO
niet aangesloten gedrag) in plaats van de verwachte `0x12`. Telkens
gefixt door DuPont-kabels visueel te inspecteren en aan te drukken op
beide aansluitingen (Pi GPIO header én Ra-01 pads). Voor veldwerk zou
de module hersolderd of mechanisch gestabiliseerd moeten worden om dit
tijdens een missie te vermijden.

---

## Zoekalgoritme

De volledige spec staat in
[`docs/zoekalgoritme-ontwerp.md`](docs/zoekalgoritme-ontwerp.md): elke
parameter met zijn herkomst, het accu-budget, de faalgevallen, en per
veldvlucht wat er is gewijzigd. Hieronder alleen de kern.

`START MISSIE` op het dashboard start `search.py` in een aparte thread.
Die doorloopt een toestandscontrole en vijf fasen:

| fase | wat er gebeurt |
|---|---|
| **0 — hovertest** | 5 s stilhangen op zoekhoogte en uitlezen wat de vluchtcontroller zelf meet: trilling, motorbalans, clipping, en de draai tijdens de klim. Deugt het niet, dan LAND op het opstijgpunt zonder één meter te vliegen |
| **1 — peilen** | 12 stops van 30° op het vaste kompasraster. Per hoek stilstaand 5 packets meten. De richting komt uit het **zwaartepunt** van de lob, niet uit de sterkste hoek |
| **2 — verifiëren** | neus op de kandidaat, 10 m die kant op, opnieuw meten. ≥ 3 dB sterker = echte bron, anders verwerpen |
| **3 — doorvliegen** | één doorlopende vlucht op 0,5 m/s dwars over de bron heen, continu metend |
| **4 — terugkruising** | 180° draaien en dezelfde lijn terug. Het middelpunt van de twee kruisingen heft een systematische meetvertraging op |
| **5 — afronden** | terug naar het opstijgpunt op zoekhoogte, positie naar de coördinaat-log met betrouwbaarheidsklasse |

Drie ontwerpkeuzes zijn tegen-intuïtief en staan er omdat het veld ze
heeft afgedwongen:

- **Stilstaand meten, niet al draaiend.** Meetwaarde en koers komen uit
  bronnen met verschillende ververssnelheden; bij een draaiende drone
  hoort een meetwaarde daardoor bij geen enkele hoek.
- **Het zwaartepunt, niet de piek.** De lob is 150-212° breed en de
  variatie erbinnen is even groot als de ruis. De sterkste hoek nemen
  zat er op twee vluchten 76° en 84° naast; het zwaartepunt haalde
  1,8° tot 11,5°.
- **Doorvliegen, niet naar de bron toe kruipen.** Een gradiënt tussen
  twee stappen is ~1,5 dB en de ruis is ~2,5 dB — je stuurt dan op
  ruis. De signaalinstort bij het passeren is 5,7 dB over 3 m en dus
  ruim boven de ruis.

Verwante modules: `pattern.py` neemt een volledig stralingsdiagram op
(36 × 10°), `approach.py` doet een rechte nadering. Beide zijn gebruikt
om de meetgegevens te verzamelen waarop `search.py` is gebouwd.

### Tests

De testsuites draaien zonder drone, tegen een nagebouwde MAVLink- en
signaalbron in `tests/sim.py`:

```bash
python3 tests/test_eenheden.py     # ~1 s
python3 tests/test_vlucht.py       # ~3 min, vier volledige vluchten
```

Ze gebruiken de **echte gemeten waarden** uit de vluchtlogs, niet
verzonnen getallen, en schrijven naar `tests/uitvoer/`. `search.DATA_DIR`
wordt daarbij omgezet zodat `data/` nooit wordt aangeraakt.

---

## Adres-lookup

Voor de operator-workflow is een **leesbare adres-aanduiding** veel
nuttiger dan ruwe lat/lon-coördinaten. Een bestrijder die de Excel-
export opent wil direct zien "Vlezenbeek, Kerkstraat 5" — niet
"50.76860, 4.27000" en dan handmatig in Google Maps gaan plakken.
Dit project gebruikt Nominatim (OpenStreetMap's gratis geocoder) in
beide richtingen.

### Forward geocoding: adres-zoek in de kaart

Operator typt in het zoekveld in de map card header een adres of
plaatsnaam (bv. "Dorpsstraat 42 Vlezenbeek"). Suggesties verschijnen
via Nominatim's `search` endpoint, gedebounced op 400 ms (rate-limit
beleid van de gratis service is 1 req/sec).

Bij selectie van een suggestie centreert en zoomt de kaart op de
gekozen locatie — **maar er wordt géén pin geplaatst**. Dat is een
bewuste UX-keuze: een melding "nest in tuin van Dorpsstraat 42" kan
gaan over een nest in een weide drie huizen verder. Het adres is
navigatie-hulp om snel in de juiste omgeving te komen; operator klikt
vervolgens zelf op de exacte nest-positie op de kaart.

Zoom-niveau bij selectie is 17 (iets uitgezoomder dan FIX_ZOOM 18 voor
de drone) zodat operator ook de omgeving rond het adres ziet.

### Reverse geocoding: adres bij elke entry

Wanneer een entry wordt opgeslagen (`POST /api/log`) roept de backend
`reverse_geocode(lat, lon)` aan. Resultaat wordt opgeslagen in het
`address`-veld van de entry in `coord-log.json`. Formaat is
gestandaardiseerd op `"Gemeente, Straat Nummer"`:

- `50.7686, 4.2700`  →  `"Vlezenbeek, Kerkstraat 5"`
- `50.7100, 4.3500`  →  `"Anderlecht, Nijverheidskaai 12"`
- Adres zonder huisnummer (weide): `"Vlezenbeek, Kerkstraat"`
- Geen straat te vinden (bos): `"Sint-Pieters-Leeuw"`
- Midden in zee of Nominatim-fout: leeg veld

### Internet-strategie: hybride

In het veld heeft de Pi typisch geen internet — alleen de Comfast
hotspot voor de operator-laptop. Daarom is de strategie hybride:

1. **Bij entry-opslag**: probeer Nominatim met 2 sec timeout. Bij
   geen internet blijft het veld leeg.
2. **Bij Excel-export**: voor elke entry zonder adres, probeer
   opnieuw via Nominatim. Bij succes wordt het adres terug
   opgeslagen in `coord-log.json` (cache-effect — volgende export
   doet geen nieuwe call meer voor dezelfde entry).
3. **Rate-limit respect**: 1.1 sec tussen ophaal-calls tijdens
   export-batch. Bij 3 opeenvolgende fails (geen internet) stopt de
   batch vroeg.
4. **Excel-fallback**: bij ontbrekend adres toont de cel
   `"(50.76860, 4.27000)"` zodat bestrijder nog steeds iets concreets
   ziet (en de Google Maps hyperlink in de laatste kolom blijft
   sowieso werken).

Workflow voor offline veldwerk:

1. Pi zonder internet → operator logt 15 nest-posities → entries
   opgeslagen zonder adres
2. Pi terug op ethernet thuis → operator klikt Excel-export → backend
   doet 15 Nominatim-calls (~16 sec voor 15 entries) → adressen
   opgeslagen in `coord-log.json`
3. Excel verschijnt met alle adressen ingevuld
4. Volgende export = direct, geen Nominatim-calls meer nodig

### Waar het adres in het dashboard verschijnt

Eens een entry een adres heeft, wordt het op drie plekken getoond:

- **Coord-log lijst** rechts van de kaart: amber-kleurige regel
  `📍 Gemeente, Straat Nummer` boven de coördinaten. Ellipsis bij te
  lange straatnamen.
- **Marker-popup** op de kaart: adres dikgedrukt onder het entry-nummer,
  dan coördinaten en hoogte eronder.
- **Edit-modal**: oranje rij `🏠 adres` boven de coördinaten-preview
  als de entry een adres heeft. Bij nieuwe entries verborgen (komt
  pas na server-side reverse-geocode bij opslag).

### Excel-export

Naast de gewone status-, tijd-, en notitie-kolommen toont de Excel
nu de Adres-kolom in plaats van aparte Latitude/Longitude-kolommen.
Die zijn weggehaald omdat ze redundant zijn met de Google Maps
hyperlink in de laatste kolom — bestrijder klikt op de link en zit
in Google Maps op de exacte coördinaten. Voor visuele inschatting
volstaat het leesbare adres in plaats van een lange decimale waarde.

De Datum-kolom is ook aangepast naar Belgische `DD/MM/YYYY` notatie
(de ISO-string met tijd erin was redundant met de aparte Tijd-kolom).

### Gedeelde code-organisatie

De Nominatim-aanroep zit in twee plaatsen:

- **Backend** `app.py` — `reverse_geocode()` voor coords → adres bij
  entry-opslag en export-tijd
- **Frontend** `static/js/nominatim.js` — `nominatimSearch()` en
  `setupAddressAutocomplete()` voor forward zoekvelden

`nominatim.js` is bewust een gedeelde module: twee consumers
(`tile-cache.js` voor offline-prefetch-gebied selectie en `map.js`
voor de kaart-navigatie) gebruiken dezelfde debounce-logica en
result-rendering. Toevoegen van een derde consumer (bv. een
adres-zoek in de log-modal) zou een paar regels code zijn.

---

## Ontwikkelen

### Git branch-strategie

Hoofdbranch: `master` — stabiele werkende versie.

Per feature een aparte branch:

```bash
git checkout -b feature/<beschrijving>
# ...werken en committen...
git checkout master
git merge --no-ff feature/<beschrijving>
```

Branches worden na merge bewust **behouden** (niet verwijderd) zodat
de volledige ontwikkel-historiek zichtbaar blijft in `git log --graph`.
Dit is een keuze voor de thesis-verdediging: een lezer kan zo per
feature inzoomen op de chronologie van een onderdeel.

**Afgerond:**

- `feature/dashboard-refactor` — modulaire CSS + JS structuur
- `feature/dashboard-redesign` — navbar met klikbare popovers, 2-koloms
  layout, oude cards/strip verwijderd
- `feature/log-status-flow` — map-click pin, status + notitie per entry,
  edit-flow per entry
- `feature/drone-command-handlers` — server-side `arm_drone` /
  `disarm_drone` / `set_mode` handlers
- `feature/xlsx-export` — gestileerde Excel-export met selectie-modal,
  verwijder-knop per log-entry
- `feature/log-backend-persistence` — log van browser-localStorage naar
  JSON-bestand op de Pi, REST endpoints voor toekomstige bestrijders-
  platform integratie
- `feature/offline-tiles` — vendor Leaflet + tile-cache route + CLI
  prefetch-tool + cache-management UI met adres-zoek via Nominatim
- `feature/thermal-camera` — Pimoroni MLX90640 via I2C, Socket.io
  frame-stream, canvas-rendering met vier paletten, normaal + baseline
  detectie-modus
- `feature/lora-packet-decoding` — SX1278 Ra-01 ontvanger via SPI met
  pyLoRa, polling-based RX, decodering van `HT,<id>,<count>` beacon-
  protocol, 3-tier dashboard layout met RSSI/SNR/packet-age
- `feature/drone-marker` — SVG paper-airplane marker die roteert op
  MAVLink heading + VespaTrack logo in navbar
- `feature/address-lookup` — Nominatim forward search in map card +
  reverse-geocoding bij log-opslag en Excel-export, gedeelde
  utility-module `nominatim.js`, favicon

### Commit messages

Project volgt [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` nieuwe functionaliteit
- `fix:` bugfix
- `refactor:` herstructurering zonder gedragswijziging
- `docs:` documentatie
- `chore:` setup, configuratie, geen functioneel effect

### Live development

Na een code-wijziging:

```bash
sudo systemctl restart hornet-tracker
```

In de browser hard-refreshen met `Ctrl+Shift+R` om CSS/JS cache te
omzeilen.

### Geen build-step

Het project gebruikt vanilla JavaScript zonder bundler of transpiler.
Modules delen functies via `window.X = X`. Dit houdt het inspecteerbaar
in de browser DevTools en vermijdt npm-tooling op de Pi.

Als het project ooit groter wordt en de window-namespace problematisch
wordt, is migratie naar ES modules (`<script type="module">`) een
incrementele stap zonder dependencies te hoeven introduceren.

---

## Roadmap

### Afgerond

- **Autonoom zoekalgoritme** (`search.py`) — zie
  [Zoekalgoritme](#zoekalgoritme):
  - Toestandscontrole vóór het zoeken: 5 s stilhangen en afkeuren op
    trilling, motorbalans, clipping van de versnellingsmeter en
    ongevraagde draai tijdens de klim. Bij afkeuring LAND op het
    opstijgpunt zonder één meter te vliegen
  - Vijf fasen: stapsgewijs peilen (12 × 30°, stilstaand gemeten),
    richting verifiëren over 10 m, doorvliegen dwars over de bron,
    terugkruisen, en afronden met een gelogde positie
  - Kandidaatkeuze op het **zwaartepunt** van de antennelob in plaats
    van de sterkste hoek; acht alternatieve schatters getoetst tegen
    dezelfde veldmetingen
  - Betrouwbaarheidsklasse (hoog / midden / laag) uit de spreiding
    tussen de twee kruisingen, meegegeven aan de coördinaat-log
  - Elke vlucht schrijft een CSV met de volledige meetreeks en de
    toestandswaarden in de kop
  - Drie keer herzien op grond van veldvluchten; elke wijziging met
    bronvermelding vastgelegd in `docs/zoekalgoritme-ontwerp.md` §9
- Rotatiemeting (`pattern.py`) — volledig stralingsdiagram opnemen in
  36 stappen van 10°, de meetreeks waarop het peilen is gebouwd
- Rechte nadering (`approach.py`) — meet wat het signaal doet bij het
  naderen en passeren van de bron
- Testsuites die zonder drone draaien (`tests/`), tegen een nagebouwde
  MAVLink- en signaalbron, met de echte gemeten waarden uit de
  vluchtlogs
- Veldcampagne: zeven veldsessies, dertien vluchten, met de beacon
  telkens op een vooraf bekende positie zodat de peilfout in graden
  becijferd kon worden
- Autonome demo-missie via het dashboard (nu de eenvoudige
  voorloper, achter `start_demo_mission` vanuit de console):
  - Voorgeprogrammeerde sequentie: opstijgen 1,5 m → hover → 360°
    draai → 2 m vooruit → hover → landen (auto-disarm)
  - Was de vlucht die bewees dat de Pi commando's kon sturen over de
    USB-C-verbinding; `START MISSIE` draait sindsdien het zoekalgoritme
  - Noodknoppen STOP & HANG (LOITER), RTL en LAND NU als backup voor
    de zender-switch
  - Pre-flight statusblok (GPS-fix, satellieten, RTL-hoogte) voordat
    de missie start
  - Veiligheidsmodel: de zender heeft altijd voorrang. Gaat de drone
    uit GUIDED (operator neemt over via SwC-switch, of failsafe), dan
    stopt het missie-script onmiddellijk. Nooit disarmen in de lucht;
    elke landing leidt tot auto-disarm door ArduCopter zelf.
  - Missie-module (`mission.py`) draait in een aparte thread, leest de
    gedeelde status-dict voor telemetrie (fire-and-forget commando's)
- MAVLink over directe USB met centrale ack-afhandeling:
  - Overgestapt van USB-TTL naar TELEM2 (`/dev/ttyUSB0`, CH340) naar
    de directe USB-verbinding met de Pixhawk (`/dev/serial/by-id/...`),
    die bidirectioneel betrouwbaar bleek waar de TTL-route pakketten
    verloor
  - `AckMailbox`: thread-safe postbus zodat alleen `mavlink_loop`
    `recv_match` aanroept en COMMAND_ACK-berichten distribueert naar
    wachtende command-handlers. Elimineert de ack-race die wegvallende
    verbinding bij knoppen en vals "piloot heeft overgenomen" gaf
  - Mode-wijzigingen bevestigd via `status['flight_mode']` (door
    `mavlink_loop` gevuld) i.p.v. een concurrerende eigen leeslus
- Real-time MAVLink telemetrie (GPS, batterij, hoogte, mode, armed)
- RTL-SDR signal detection met automatische baseline + peak-hold
  (als legacy/fallback pad behouden in SignalSource interface)
- Leaflet kaart (lokaal gehost) met satelliet/stratenplan en drone-tracking
- Pi shutdown/reboot via dashboard
- Modulaire CSS + JS structuur
- Navbar met klikbare popovers (batterij, Pi-grondstation, GPS)
- 2-koloms layout voor minimaal scrollen op 1920 × 1080
- Comfast hotspot voor veldgebruik zonder router
- Coördinaat-log met:
  - drone-positie loggen én manueel pinnen op kaart
  - status per entry (gemeld / wordt onderzocht / waargenomen /
    bestreden / vals alarm / leeg laten)
  - vrije notitie per entry
  - source-tracking (drone vs manueel) met visuele iconen
  - bewerken van bestaande entries via potlood-knop
  - verwijderen van individuele entries via prullenbak-knop
  - server-side persistentie op de Pi (JSON-bestand, overleeft refresh,
    cross-browser zichtbaar, blijft bij Pi-reboot)
  - REST API endpoints (GET/POST/PUT/DELETE) voor toekomstige bestrijders-
    platform integratie
  - Google Maps deeplinks
- Server-side handlers voor `arm_drone` / `disarm_drone` / `set_mode`
- Gestileerde Excel-export met:
  - selectie-modal (operator kiest welke entries hij wil downloaden)
  - "Alles selecteren" toggle in email-inbox stijl
  - status-cellen ingekleurd per status
  - klikbare Google Maps hyperlinks per entry
  - bevroren header-rij bij scrollen
  - bestandsnaam met Belgische datum-notatie
  - Adres-kolom (ipv lat/lon) met automatische reverse-geocoding voor
    entries zonder adres, Belgische DD/MM/YYYY datum-notatie
- Offline kaart-tiles voor veldwerk zonder internet:
  - Lokaal gehoste Leaflet library (geen CDN-afhankelijkheid)
  - Flask tile-route met lokale cache + internet-fallback
  - Drie tile-bronnen: OSM stratenplan, ArcGIS satelliet, straatnamen overlay
  - CLI tool `prefetch_tiles.py` voor bulk-downloads
  - Cache-management UI in het dashboard met stats, prefetch en wissen
  - Adres-zoeken via Nominatim voor gemakkelijke selectie van prefetch-gebieden
  - Live progress-bar tijdens prefetch via status-polling
- Thermisch beeld in dashboard:
  - Pimoroni MLX90640 via I2C, Adafruit CircuitPython library
  - Backend thermal_loop met auto-reconnect bij I2C-glitches
  - Frame-stream via aparte Socket.io `thermal_frame` event (~5 KB payload)
  - Canvas-rendering met nearest-neighbor upscaling (scherpe pixels)
  - Hybride scaling die ruis-amplificatie in uniforme scènes voorkomt
  - Vier paletten: Iron, Inferno, Grijswaarden, Rainbow (custom dropdown)
  - Baseline detectie-modus: snapshot huidige scène, daarna alleen
    afwijkingen tonen — geschikt voor hot-spot zoektocht
  - Min/Gem/Max/FPS statistieken altijd zichtbaar
- LoRa packet-decoding ter vervanging van RTL-SDR energy detection:
  - SX1278 Ra-01 ontvanger via SPI met pyLoRa library
  - Polling-based RX in achtergrond-thread (interrupt-callbacks werken
    niet betrouwbaar met rpi-lgpio op Pi OS Bookworm)
  - Decoderen van `HT,<id>,<count>` beacon-protocol met CRC-check
  - Echte RSSI per packet in dBm + SNR (Signal-to-Noise Ratio in dB)
  - Drie-tier dashboard layout: RSSI + detectie-badge + absolute bar,
    SNR + packet-age, packets count + tracker ID
  - Detectie op basis van packet-age (< 3 s = signaal actief),
    silence-floor op -120 dBm bij stilte
  - SignalSource interface met RTL-SDR fallback voor backup/testing
  - Bestaande dashboard-flow (signal-card layout, status_update events)
    behouden — `SIGNAL_SOURCE` config switch tussen modi
- Drone-marker als georiënteerde pijl in plaats van emoji:
  - SVG paper-airplane vorm met oranje fill en witte rand
  - Roteert op MAVLink GLOBAL_POSITION_INT.hdg (magnetometer-heading)
  - Smooth rotatie via CSS transition (0.3s) zodat marker draait i.p.v.
    abrupt springt bij richting-verandering
  - Werkt los van GPS-fix: heading is direct beschikbaar zodra
    magnetometer-data binnenkomt
- VespaTrack logo in navbar + SVG favicon in browser-tab
- Adres-lookup voor leesbare locatie-aanduiding:
  - Forward geocoding in map card header voor snelle navigatie naar
    een gemelde locatie zonder visueel zoeken
  - Reverse geocoding bij log-opslag (Pi-side) zodat elke entry een
    leesbare "Gemeente, Straat Nummer" krijgt
  - Hybride internet-strategie: bij ontbrekend adres alsnog ophalen
    tijdens Excel-export en cachen in coord-log.json (geen herhaalde
    Nominatim-calls)
  - Adres getoond in coord-log lijst, marker-popups en edit-modal
  - Geen automatische pin bij adres-selectie: operator behoudt
    controle over de exacte log-positie (melders weten zelden exact
    waar het nest zit, alleen het meld-adres)
  - Gedeelde `nominatim.js` utility voor consistente debounce en
    rate-limit-handling tussen meerdere consumers in het dashboard

- Gebruikersonderzoek met de doelgroep — twee testsessies met erkende
  hoornaarverdelgers, die het dashboard zonder uitleg vooraf moesten
  bedienen. Wat eruit voortkwam en in de interface zit:
  - **Tabbladen** (Veld / Thermisch / Coördinaten) zodat de kern op één
    scherm past — scrollen werd niet spontaan ontdekt
  - **SNR hernoemd naar "signaalgetrouwheid"**; geen van beide
    deelnemers kon de afkorting verklaren
  - **Balk met kleurindicatie** voor signaalsterkte en
    signaalgetrouwheid, geschaald op het werkelijk gemeten bereik —
    de deelnemers stelden zelf een kleurenbalk voor boven een getal
  - **Instelbare zoekhoogte**, nadat ze aankaartten dat populieren 30
    tot 50 m halen
  - **"Drone pad" hernoemd naar "Afgelegde weg"** — de functie werd als
    zeer waardevol beoordeeld, maar de naam vertelde dat niet
  - **Downloadknop ook bij de coördinatenlijst**, op de plek waar ze
    hem zochten
  - Statusvensters in de navbar ontdaan van jargon (TELEM2, SSID,
    wlan1, "3D fix") ten gunste van gewone taal

### In planning

- Ra-01 module hersolderen of mechanisch stabiliseren om tijdens
  veldwerk geen contact-issues te krijgen
- Range-test in vrij veld met TX-power 20 dBm op LiPo om realistische
  bereik-cijfers te krijgen voor het thesis-rapport. Alle huidige
  metingen zijn op 2 dBm gedaan
- Polarisatietest om het 45 dB link-tekort te lokaliseren: as van de
  beacon-cilinder evenwijdig aan de **elementen** van de Yagi, drone
  90° kantelen, RSSI aflezen. Twee minuten werk, en het bepaalt de
  peilnauwkeurigheid van het hele systeem
- CRC-fouten tellen in de ontvangstlus. Tijdens de doorvluchten komt
  ~driekwart van de verwachte packets binnen; zonder die teller is
  "wel ontvangen maar corrupt" niet te scheiden van "er kwam niets"
- Onderscheid tussen ingestelde zoekhoogte en actuele vlieghoogte
  duidelijker maken in het dashboard — kwam uit de tweede
  gebruikerstest
- Herbouw van het droneplatform (frame, motoren, ESC's), met de
  volledige radioketen ongewijzigd mee zodat alle eerdere metingen
  vergelijkbaar blijven. De toestandscontrole uit fase 0 dient als
  acceptatietest tegen de vastgelegde basislijn

### Toekomstige uitbreidingen (visie, niet gepland)

- Bestrijders-platform: webapp/mobile app waar erkende bestrijders
  gevalideerde nest-locaties kunnen zien, "claimen" zodat geen twee
  partijen tegelijk uitrukken, en status-updates kunnen pushen
  (waargenomen → bestreden). Vereist authenticatie, certificaat-upload,
  GDPR-compliance. Wordt eerst uitgewerkt als ontwerpvisie in het
  thesis-rapport.
- Multi-tracker uitbreiding: meerdere beacons gelijktijdig in de lucht
  met verschillende ID's in de payload. Vereist coördinatie tussen
  beacons (TDMA, of verschillende frequenties/SF's per tracker) om
  co-channel collisions te vermijden. Backend en frontend zijn al
  voorbereid via het tracker-ID veld.
- Auto-discovery van nestlocatie op basis van signaal-piek + GPS-positie
  correlatie
- Computer-vision op het thermische beeld voor automatische
  nest-detectie (warm cluster van ≥ N pixels boven baseline → markeer
  positie op kaart)

---

## Licentie

Bachelorthesis-project. All rights reserved.

---

## Auteur

**Kian Van den bussche**
Erasmushogeschool Brussel — Multimedia en Creatieve Technologie

---

## Bronvermelding

Delen van deze codebase en documentatie zijn ontwikkeld met assistentie
van Anthropic's Claude AI als pair-programming tool.

- **Tool** Claude (Anthropic)
- **Gesprek** <https://claude.ai/share/2ab5ba1f-5a95-46e3-920d-3d8ff4d12a5c>
              <https://claude.ai/share/44279bf8-833e-4d1e-8942-9bb817abc98f>
- **Datum** mei - augustus 2026