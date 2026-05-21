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

- **Backend** — Python 3, Flask, Flask-SocketIO, threading-based
  background loops voor signal/wifi/mavlink
- **MAVLink** — pymavlink, bidirectioneel: telemetrie ontvangen
  (GPS, batterij, mode, armed) én commando's sturen (arm/disarm/mode)
- **RF-detectie** — pyrtlsdr met FFT-based energy detection, baseline
  + peak-hold detectie
- **Excel-export** — openpyxl voor server-side generatie van
  gestileerde XLSX-bestanden uit log-entries
- **Frontend** — vanilla JavaScript (geen build-step), Leaflet voor de
  kaart, fetch-API voor REST-calls naar de backend log-persistentie

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
│                                   Excel-export endpoint
├── README.md                       dit bestand
├── requirements.txt                Python dependencies
├── .gitignore
│
├── templates/
│   └── dashboard.html              pure markup, geen inline JS/CSS
│
├── data/                           runtime persistente data (gitignored)
│   ├── README.md                   uitleg over wat hier hoort
│   └── coord-log.json              gelogde entries, gegenereerd op de Pi
│
└── static/
    ├── socket.io.min.js            client library (vendored)
    │
    ├── css/                        modulaire styling per component
    │   ├── base.css                reset, body, gedeelde kleuren, responsive
    │   ├── layout.css              2-koloms dashboard layout
    │   ├── navbar.css              vaste navbar bovenaan met status-popovers
    │   ├── cards.css               grid cards + status rows + signaalbalkjes
    │   ├── signal.css              LoRa signal card
    │   ├── map.css                 Leaflet kaart + GPS-waiting badge
    │   ├── coord-log.css           gelogde coördinaten + status badges + toast
    │   ├── controls.css            knoppen + modals + log-formulier + export-modal
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
        ├── modals.js               shutdown/reboot/arm/log/export dialoogvensters
        └── main.js                 bootstrap (socket + init + log fetchen van Pi)
```

### Frontend module-volgorde

`dashboard.html` laadt de JS-modules in deze volgorde:

1. `utils.js`, `map.js`, `coord-log.js`, `drone-controls.js`,
   `signal-display.js` — definiëren functies op `window`, geen socket nodig.
2. `socket-handlers.js` — handlers die `window.socket` gebruiken.
3. `modals.js` — dialog-logica voor shutdown/reboot/arm/log/export.
4. `navbar.js` — popover open/sluit logica voor navbar-items.
5. `main.js` — bootstrap: maakt de socket aan, haalt log van de Pi via
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

> **Offline-werking is nog niet volledig.** De kaart laadt momenteel
> tiles van OpenStreetMap/ArcGIS over internet, dus zonder uplink
> verschijnt het dashboard wel maar blijft de kaart leeg. Dit wordt
> opgelost in de geplande `feature/offline-tiles` branch (vendored
> Leaflet library + pre-cached tiles voor België).
>
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

**Lopende (wacht op hardware):**

- `feature/thermal-camera` — Pimoroni MLX90640 integratie via I2C
- `feature/lora-packet-decoding` — Ra-01 SX1278 in productie zetten ter
  vervanging van RTL-SDR energy detection

**Gepland:**

- `feature/offline-tiles` — vendor Leaflet + pre-cached
  OpenStreetMap-tiles voor België zodat dashboard werkt zonder internet


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
- Leaflet kaart met satelliet/stratenplan en drone-tracking
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

### In planning

- MLX90640 warmtecamera integratie via I2C (Pimoroni 55° onderweg)
- Ra-01 LoRa-pakketdecodering ter vervanging van RTL-SDR energy detection
- Offline kaart-tiles voor echt veldgebruik zonder internet
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
