# 🐝 Hornet Tracker — VespaTrack Ground Station

Real-time grondstation-dashboard voor het lokaliseren van Aziatische
hoornaarnesten met een autonome RF-tracking drone. Bachelorthesis-project
aan Erasmushogeschool Brussel, opleiding Multimedia en Creatieve
Technologie.

De drone — VespaTrack — draagt een RF-ontvanger en patrouilleert door
een gebied terwijl ze het signaal volgt van een kleine LoRa-beacon die
op een testobject is geplakt. Het grondstation toont in real-time de
drone-positie op een satellietkaart, het ontvangen signaal, en biedt
bediening van de vlucht.

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
                                   │  - RTL-SDR / Ra-01      │
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
Commando's vanuit het dashboard (arm, disarm, mode-wijziging) gaan
dezelfde weg terug naar de Pi en worden via MAVLink doorgestuurd naar
de Pixhawk.

---

## Hardware

### Drone-platform

- **Tarot 650 Sport frame** — drone airframe
- **Pixhawk 6C Mini + ArduCopter 4.6.3** — flight controller
- **Raspberry Pi 4** — companion computer (dashboard + tracking-logica)

### RF-tracking

- **SX1278 Ra-01 LoRa-ontvanger** — beacon-signaal ontvangen via SPI
- **RTL-SDR USB** — tijdelijke vervanger voor Ra-01 tijdens ontwikkeling

### Sensoren

- **Pimoroni MLX90640 55°** — thermische camera (32×24 pixels) voor
  visuele bevestiging van nest op korte afstand

### Communicatie

- **USB-TTL CH340** — Pi ↔ Pixhawk verbinding (MAVLink op TELEM2)
- **SiK Radio 433 MHz** — backup-telemetrie naar QGroundControl
- **Comfast MT7612U** — USB WiFi-adapter op de Pi, host van veldhotspot

---

## Software stack

- **Backend** — Python 3, Flask, Flask-SocketIO met threading async mode,
  threading-based background loops voor signal/wifi/mavlink
- **MAVLink** — pymavlink, bidirectioneel: telemetrie ontvangen
  (GPS, batterij, mode, armed) én commando's sturen (arm/disarm/mode)
- **RF-detectie** — pyrtlsdr met FFT-based energy detection, baseline
  + peak-hold detectie
- **Excel-export** — openpyxl voor server-side generatie van
  gestileerde XLSX-bestanden uit log-entries
- **Frontend** — vanilla JavaScript (geen build-step), Leaflet voor de
  kaart (lokaal gehost), fetch-API voor REST-calls naar de backend
- **Offline tiles** — Flask-route serveert lokaal gecachte map-tiles
  uit `data/tiles/`, met internet-fallback bij cache-miss
- **Adres-geocoding** — Nominatim API (OpenStreetMap) voor adres → lat/lon
  resolution bij offline-tile prefetch

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
sudo apt install python3-pip git
pip3 install -r requirements.txt
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

### 3. Systemd service registreren

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
Restart=on-failure
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

### 4. Udev-rule voor Pixhawk

Zodat de Pixhawk altijd verschijnt als `/dev/ttyPixhawk` in plaats van
een wisselende `/dev/ttyUSB0`:

```text
# /etc/udev/rules.d/99-pixhawk.rules
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="7523", SYMLINK+="ttyPixhawk"
```

Herladen na aanpassing:

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

### 5. Comfast hotspot configureren (optioneel, voor veldwerk)

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
│                                   RTL-SDR, WiFi status, command handlers,
│                                   Excel-export endpoint, tile-cache routes
├── prefetch_tiles.py               CLI tool: bulk-download tiles voor offline gebruik
├── README.md                       dit bestand
├── requirements.txt                Python dependencies
├── .gitignore
│
├── templates/
│   └── dashboard.html              pure markup, geen inline JS/CSS
│
├── data/                           runtime persistente data (gitignored)
│   ├── README.md                   uitleg over wat hier hoort
│   ├── coord-log.json              gelogde entries, gegenereerd op de Pi
│   └── tiles/                      lokaal gecachte map-tiles per source
│       ├── osm/<z>/<x>/<y>.png     OpenStreetMap stratenplan
│       ├── sat/<z>/<x>/<y>.png     ArcGIS satelliet (JPEG inhoud)
│       └── hyb/<z>/<x>/<y>.png     ArcGIS straatnamen overlay
│
└── static/
    ├── socket.io.min.js            client library (vendored)
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
    │   ├── signal.css              LoRa signal card
    │   ├── map.css                 Leaflet kaart + GPS-waiting badge
    │   ├── coord-log.css           gelogde coördinaten + status badges + toast
    │   ├── controls.css            knoppen + modals + log/export/tile-cache modals
    │   └── thermal.css             warmtecamera (in voorbereiding)
    │
    └── js/                         modulaire JavaScript per concern
        ├── utils.js                rssi helpers, toast, card status
        ├── map.js                  Leaflet init, drone marker, trail, click-handler
        ├── coord-log.js            log entries, map-click pin, status/notitie,
        │                           edit, delete, REST naar backend, Excel-export
        ├── drone-controls.js       arm/disarm/mode + command result handler
        ├── signal-display.js       LoRa signal card + baseline reset
        ├── navbar.js               popover toggle + click-outside-to-close
        ├── socket-handlers.js      connect/disconnect/status_update dispatch
        ├── modals.js               shutdown/reboot/arm/log/export/tile-cache dialogen
        ├── tile-cache.js           cache stats + prefetch UI + Nominatim adres-zoek
        └── main.js                 bootstrap (socket + init + log fetchen van Pi)
```

### Frontend module-volgorde

`dashboard.html` laadt de JS-modules in deze volgorde:

1. `utils.js`, `map.js`, `coord-log.js`, `drone-controls.js`,
   `signal-display.js` — definiëren functies op `window`, geen socket nodig.
2. `socket-handlers.js` — handlers die `window.socket` gebruiken.
3. `modals.js` — dialog-logica voor alle modals.
4. `tile-cache.js` — offline tiles UI + Nominatim adres-zoek.
5. `navbar.js` — popover open/sluit logica voor navbar-items.
6. `main.js` — bootstrap: maakt de socket aan, haalt log van de Pi via
   `GET /api/log`, initialiseert alles na `DOMContentLoaded`.

Alle module-functies worden op `window` gezet zodat inline
`onclick="..."` handlers in de HTML rechtstreeks werken zonder
build-step of bundler.

---

## Netwerktoegang

Het dashboard is bereikbaar op poort 5000. Afhankelijk van de setup:

| URL                              | Context                                  |
| -------------------------------- | ---------------------------------------- |
| `http://hoornaar-tracker:5000`   | via mDNS (thuisnetwerk ethernet)         |
| `http://192.168.1.3:5000`        | direct IP op thuisnetwerk                |
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

## Ontwikkelen

### Git branch-strategie

Hoofdbranch: `main` — stabiele werkende versie.

Per feature een aparte branch:

```bash
git checkout -b feature/<beschrijving>
# ...werken en committen...
git checkout main
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

**Lopende (wacht op hardware):**

- `feature/thermal-camera` — Pimoroni MLX90640 integratie via I2C
- `feature/lora-packet-decoding` — Ra-01 SX1278 in productie zetten ter
  vervanging van RTL-SDR energy detection

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

- Real-time MAVLink telemetrie (GPS, batterij, hoogte, mode, armed)
- RTL-SDR signal detection met automatische baseline + peak-hold
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
- Offline kaart-tiles voor veldwerk zonder internet:
  - Lokaal gehoste Leaflet library (geen CDN-afhankelijkheid)
  - Flask tile-route met lokale cache + internet-fallback
  - Drie tile-bronnen: OSM stratenplan, ArcGIS satelliet, straatnamen overlay
  - CLI tool `prefetch_tiles.py` voor bulk-downloads
  - Cache-management UI in het dashboard met stats, prefetch en wissen
  - Adres-zoeken via Nominatim (OpenStreetMap geocoder) voor gemakkelijke
    selectie van prefetch-gebieden
  - Live progress-bar tijdens prefetch via status-polling

### In planning

- MLX90640 warmtecamera integratie via I2C (Pimoroni 55° onderweg)
- Ra-01 LoRa-pakketdecodering ter vervanging van RTL-SDR energy detection
- Layout-finetuning na thermal- en LoRa-integratie zodat alle cards
  exact passen op 1920 × 1080 zonder scrollen
- Besturing card collapse-toggle (mode-knoppen verbergen tijdens vlucht
  om ruimte vrij te maken voor kaart en warmtebeeld)

### Toekomstige uitbreidingen (visie, niet gepland)

- Bestrijders-platform: webapp/mobile app waar erkende bestrijders
  gevalideerde nest-locaties kunnen zien, "claimen" zodat geen twee
  partijen tegelijk uitrukken, en status-updates kunnen pushen
  (waargenomen → bestreden). Vereist authenticatie, certificaat-upload,
  GDPR-compliance. Wordt eerst uitgewerkt als ontwerpvisie in het
  thesis-rapport.
- Auto-discovery van nestlocatie op basis van signaal-piek + GPS-positie
  correlatie

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
- **Datum** mei 2026
