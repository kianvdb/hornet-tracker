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

- **GY-MCU90640 (MLX90640)** — thermische camera voor visuele bevestiging

### Communicatie

- **USB-TTL CH340** — Pi ↔ Pixhawk verbinding (MAVLink op TELEM2)
- **SiK Radio 433 MHz** — backup-telemetrie naar QGroundControl

---

## Software stack

- **Backend** — Python 3, Flask, Flask-SocketIO
- **MAVLink** — pymavlink (commando's + telemetrie van Pixhawk)
- **RF-detectie** — pyrtlsdr met FFT-based energy detection
- **Frontend** — vanilla JavaScript (geen build-step), Leaflet voor de kaart

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
├── app.py                          Flask + Socket.io backend
├── README.md                       dit bestand
├── requirements.txt                Python dependencies
├── .gitignore
│
├── templates/
│   └── dashboard.html              pure markup, geen inline JS/CSS
│
└── static/
    ├── socket.io.min.js            client library (vendored)
    │
    ├── css/                        modulaire styling per component
    │   ├── base.css                reset, body, gedeelde kleuren, responsive
    │   ├── cards.css               quick-status strip + grid cards
    │   ├── signal.css              LoRa signal card
    │   ├── map.css                 Leaflet kaart + GPS-waiting badge
    │   ├── coord-log.css           gelogde coördinaten + toast
    │   ├── controls.css            knoppen + modals
    │   ├── thermal.css             warmtecamera
    │   └── navbar.css              navbar
    │
    └── js/                         modulaire JavaScript per concern
        ├── utils.js                rssi helpers, toast, card status
        ├── map.js                  Leaflet init, drone marker, trail
        ├── coord-log.js            log positie, copy, export, clear
        ├── drone-controls.js       arm/disarm/mode + command timeout
        ├── signal-display.js       LoRa signal card + baseline reset
        ├── socket-handlers.js      connect/disconnect/status_update
        ├── modals.js               shutdown/reboot/arm dialoogvensters
        └── main.js                 bootstrap (socket + init)
```

### Frontend module-volgorde

`dashboard.html` laadt de JS-modules in deze volgorde:

1. `utils.js`, `map.js`, `coord-log.js`, `drone-controls.js`,
   `signal-display.js` — definiëren functies op `window`, geen socket nodig.
2. `socket-handlers.js` — handlers die `window.socket` gebruiken.
3. `modals.js` — gebruikt `window.socket` voor shutdown/reboot.
4. `main.js` — bootstrap: maakt de socket aan en initialiseert alles
   na `DOMContentLoaded`.

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

De Pi draait optioneel een eigen WiFi-hotspot op `wlan1` zodat de
veldlaptop in het veld zonder router verbinding kan maken.

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

Lopende en geplande feature-branches:

- `feature/dashboard-refactor` — modulaire structuur (afgerond)
- `feature/thermal-camera` — MLX90640 integratie via I2C
- `feature/navbar-redesign` — navbar zodat dashboard zonder scrollen werkt
- `feature/lora-packet-decoding` — Ra-01 in productie zetten
- `feature/drone-command-handlers` — `arm_drone` / `disarm_drone` /
  `set_mode` server-side implementatie

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
- RTL-SDR signal detection met automatische baseline
- Leaflet kaart met satelliet/stratenplan en drone-tracking
- Coördinaat-log met Google Maps deeplinks en CSV-export
- Pi shutdown/reboot via dashboard
- Modulaire CSS + JS structuur

### In planning

- MLX90640 warmtecamera integratie via I2C
- Navbar redesign zodat het hele dashboard zonder scrollen werkt
  op 1920 × 1080
- Ra-01 LoRa-pakketdecodering ter vervanging van RTL-SDR energy detection
- Server-side handlers voor `arm_drone` / `disarm_drone` / `set_mode`
- Auto-discovery van best hoornaarnest-locatie op basis van signaal-piek
  + GPS-positie correlatie

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