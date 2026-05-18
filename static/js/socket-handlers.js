/**
 * SOCKET-HANDLERS — algemene Socket.io event handlers
 *
 * Bevat:
 *  - registerCoreSocketHandlers()  connect, disconnect, status_update
 *
 * Wat hier NIET zit (omdat ze tightly coupled zijn aan hun eigen module):
 *  - command_result      -> drone-controls.js
 *  - baseline_status     -> signal-display.js
 *
 * Wat hier WEL zit:
 *  - connect / disconnect:  Pi online/offline status in oude #pi-status veld
 *                           (verhuist naar navbar in Doel 1)
 *  - status_update:         centrale dispatcher die elk deel van de status
 *                           doorduwt naar de juiste module
 *
 * De status_update handler is bewust een "dispatcher": hij leest het
 * data-object van de server en roept de relevante module-functies aan.
 * Dit houdt de socket-laag dun en de UI-logica per module bij elkaar.
 *
 * Afhankelijkheden:
 *  - window.socket               Socket.io client (gezet door main.js)
 *  - window.updateSignalCard()   signal-display.js
 *  - window.updateModeButtons()  drone-controls.js
 *  - window.updateMap()          map.js
 *  - window.rssiToBars()         utils.js
 *  - window.rssiLabel()          utils.js
 *  - window.updateBars()         utils.js
 *  - window.setCardStatus()      utils.js
 *  - window.barsToStatusLevel()  utils.js
 *  - window.hideModals()         modals.js (gebruikt bij socket reconnect)
 */

/**
 * Registreer connect, disconnect, en status_update.
 * Wordt aangeroepen vanuit main.js direct na socket creatie.
 */
function registerCoreSocketHandlers() {

    // ============================================
    // CONNECT — Pi online
    // ============================================
    window.socket.on('connect', function() {
        // Pi-status veld in oude verbindingen-card (verdwijnt in Doel 1)
        const piEl = document.getElementById('pi-status');
        if (piEl) {
            piEl.textContent = 'ONLINE';
            piEl.className   = 'status-value connected';
        }
        // Sluit eventueel openstaande shutdown/reboot modals
        // (als socket reconnect na reboot, modal mag weg)
        window.hideModals();
    });

    // ============================================
    // DISCONNECT — Pi offline
    // ============================================
    window.socket.on('disconnect', function() {
        const piEl = document.getElementById('pi-status');
        if (piEl) {
            piEl.textContent = 'OFFLINE';
            piEl.className   = 'status-value disconnected';
        }
    });

    // ============================================
    // STATUS_UPDATE — dispatcher
    // ============================================
    window.socket.on('status_update', dispatchStatusUpdate);
}

/**
 * Dispatch een status-update bericht naar alle modules die er iets
 * mee moeten doen. Gehouden als losse functie zodat hij later testbaar
 * is en eenvoudig uit te breiden bij nieuwe modules (bv. thermal camera).
 *
 * @param {Object} data  status object van server (zie status dict in app.py)
 */
function dispatchStatusUpdate(data) {
    // --- LoRa signal card ---
    window.updateSignalCard(data);

    // --- Verbindingen card (Pixhawk + GPS) ---
    // Deze velden verdwijnen in Doel 1 (navbar refactor). Voor nu
    // updaten we ze inline zodat huidige UI blijft werken.
    updateConnectionsCard(data);

    // --- Map (drone positie + trail) ---
    window.updateMap(data.gps_lat || 0, data.gps_lon || 0, data.altitude || 0);

    // --- Drone status card (armed, mode, batterij, hoogte) ---
    // Verdwijnt grotendeels in Doel 1. Voor nu inline.
    updateDroneStatusCard(data);
    window.updateModeButtons(data.flight_mode);

    // --- Quick status cards bovenaan (batterij, wifi, SiK) ---
    // Verdwijnen volledig in Doel 1 (vervangen door navbar).
    updateBatteryQuickCard(data);
    updateBatteryNavbar(data);
    updateWifiNavbar(data);
    updateGpsNavbar(data);
    updateWifiQuickCard(data);
    updateTelemQuickCard(data);
}

// ============================================
// CARD UPDATE HELPERS (verdwijnen grotendeels in Doel 1)
// ============================================

/**
 * Update de "Verbindingen" card: Pixhawk online/offline, GPS fix status,
 * en het sats-veld onder de map.
 */
function updateConnectionsCard(data) {
    const pixEl = document.getElementById('pixhawk-status');
    if (pixEl) {
        pixEl.textContent = data.pixhawk_connected ? 'ONLINE' : 'OFFLINE';
        pixEl.className   = 'status-value ' + (data.pixhawk_connected ? 'connected' : 'disconnected');
    }

    const gpsEl = document.getElementById('gps-status');
    if (gpsEl) {
        gpsEl.textContent = data.gps_fix
            ? 'FIX (' + (data.gps_satellites || '?') + ' sats)'
            : 'GEEN FIX';
        gpsEl.className = 'status-value ' + (data.gps_fix ? 'connected' : 'disconnected');
    }

    // Sats veld onder de kaart (blijft mogelijk staan in Doel 1)
    const satsEl = document.getElementById('map-sats');
    if (satsEl) satsEl.textContent = data.gps_satellites || '--';
}

/**
 * Update de "Drone Status" card: armed badge, mode, batterij, hoogte.
 */
function updateDroneStatusCard(data) {
    const armedEl = document.getElementById('armed-status');
    if (armedEl) {
        armedEl.textContent = data.armed ? 'ARMED' : 'DISARMED';
        armedEl.className   = 'status-badge ' + (data.armed ? 'armed' : 'disarmed');
    }

    const modeEl = document.getElementById('flight-mode');
    if (modeEl) modeEl.textContent = data.flight_mode;

    const batEl = document.getElementById('battery');
    if (batEl) batEl.textContent = data.battery_voltage.toFixed(1) + ' V (' + data.battery_percent + '%)';

    const altEl = document.getElementById('altitude');
    if (altEl) altEl.textContent = data.altitude.toFixed(1) + ' m';
}

/**
 * Update de bovenste batterij quick-card.
 *
 * Status-logica:
 *   - Pixhawk offline           -> bad (rood) - geen meting beschikbaar
 *   - <= 15%                    -> bad (rood) + critical fill
 *   - <= 30%                    -> warn (oranje) + low fill
 *   - <= 50%                    -> ok (geel)
 *   - > 50%                     -> good (groen)
 */
function updateBatteryQuickCard(data) {
    const batPct = data.battery_percent || 0;

    document.getElementById('quick-battery-percent').textContent = batPct + '%';
    document.getElementById('quick-battery-voltage').textContent =
        (data.battery_voltage || 0).toFixed(2) + ' V';

    const fill = document.getElementById('battery-fill');
    fill.style.width = Math.max(0, Math.min(100, batPct)) + '%';
    fill.classList.remove('low', 'critical');

    let level = 'good';
    if (batPct <= 15)      { fill.classList.add('critical'); level = 'bad';  }
    else if (batPct <= 30) { fill.classList.add('low');      level = 'warn'; }
    else if (batPct <= 50) { level = 'ok'; }

    // Pixhawk offline overruled alles (geen betrouwbare meting)
    if (!data.pixhawk_connected) level = 'bad';

    window.setCardStatus('quick-battery', level);
}

/**
 * Update de batterij-indicator in de navbar.
 * Naast de hoofd-waarde (% in navbar) wordt ook de popover-content gevuld.
 *
 * Status-logica is identiek aan updateBatteryQuickCard.
 */
function updateBatteryNavbar(data) {
    const batPct = data.battery_percent || 0;
    const voltage = (data.battery_voltage || 0).toFixed(2);

    // Hoofd-waarde in navbar
    document.getElementById('nav-battery-percent').textContent = batPct + '%';
    document.getElementById('nav-battery-voltage').textContent = voltage + ' V';

    // Popover-content
    document.getElementById('pop-battery-percent').textContent = batPct + '%';
    document.getElementById('pop-battery-voltage').textContent = voltage + ' V';

    const pixEl = document.getElementById('pop-pixhawk-status');
    if (data.pixhawk_connected) {
        pixEl.textContent = 'ONLINE';
        pixEl.style.color = '#4ade80';
    } else {
        pixEl.textContent = 'OFFLINE';
        pixEl.style.color = '#f87171';
    }

    // Status-kleur op de navbar-item zelf
    let level = 'good';
    if (batPct <= 15)      level = 'bad';
    else if (batPct <= 30) level = 'warn';
    else if (batPct <= 50) level = 'ok';
    if (!data.pixhawk_connected) level = 'bad';

    const navItem = document.getElementById('nav-battery');
    navItem.classList.remove('status-good', 'status-ok', 'status-warn', 'status-bad');
    navItem.classList.add('status-' + level);
}


/**
 * Update de WiFi (Pi ↔ Grondstation) indicator in de navbar.
 * Toont RSSI van de zwakste verbonden client, aantal clients, en
 * verbergt de RSSI wanneer er niemand verbonden is.
 */
function updateWifiNavbar(data) {
    const navRssi    = document.getElementById('nav-wifi-rssi');
    const navClients = document.getElementById('nav-wifi-clients');
    const navItem    = document.getElementById('nav-wifi');

    if (data.wifi_connected && data.wifi_clients > 0) {
        navRssi.textContent    = data.wifi_rssi + ' dBm';
        navClients.textContent = data.wifi_clients +
            (data.wifi_clients === 1 ? ' client' : ' clients');
    } else {
        navRssi.textContent    = 'N/A';
        navClients.textContent = '0 clients';
    }

    // Popover-content
    document.getElementById('pop-wifi-rssi').textContent =
        data.wifi_connected ? data.wifi_rssi + ' dBm' : 'N/A';
    document.getElementById('pop-wifi-clients').textContent = data.wifi_clients || 0;

    const labEl = document.getElementById('pop-wifi-label');
    if (data.wifi_connected) {
        const lab = window.rssiLabel(data.wifi_rssi);
        labEl.textContent = lab.text;
        labEl.className   = lab.cls;
    } else {
        labEl.textContent = 'Geen verbinding';
        labEl.className   = 'label-bad';
    }

    // Status-kleur op navbar-item
    const bars = data.wifi_connected ? window.rssiToBars(data.wifi_rssi) : 0;
    const level = window.barsToStatusLevel(bars, data.wifi_connected && data.wifi_clients > 0);
window.updateBars('nav-wifi-bars', bars);
    navItem.classList.remove('status-good', 'status-ok', 'status-warn', 'status-bad');
    navItem.classList.add('status-' + level);
}


/**
 * Update de GPS indicator in de navbar.
 *
 * Toont één van drie states als hoofd-waarde:
 *  - "Pixhawk offline"   (rood)  — geen MAVLink verbinding
 *  - "Geen fix"          (oranje)— Pixhawk online maar geen 3D fix
 *  - "<N> sats"          (groen) — 3D fix, met aantal satellieten
 *
 * Sub-label toont live hoogte zodra er een fix is.
 * Popover toont volledige details inclusief lat/lon.
 */
function updateGpsNavbar(data) {
    const navStatus = document.getElementById('nav-gps-status');
    const navAlt    = document.getElementById('nav-gps-altitude');
    const navItem   = document.getElementById('nav-gps');

    let level;

    if (!data.pixhawk_connected) {
        navStatus.textContent = 'Pixhawk offline';
        navAlt.textContent    = '';
        level = 'bad';
    } else if (!data.gps_fix) {
        navStatus.textContent = 'Geen fix';
        navAlt.textContent    = (data.gps_satellites || 0) + ' sats';
        level = 'warn';
    } else {
        navStatus.textContent = (data.gps_satellites || '?') + ' sats';
        navAlt.textContent    = (data.altitude || 0).toFixed(1) + ' m';
        level = 'good';
    }

    navItem.classList.remove('status-good', 'status-ok', 'status-warn', 'status-bad');
    navItem.classList.add('status-' + level);

    // Popover-content
    const pixEl = document.getElementById('pop-gps-pixhawk');
    pixEl.textContent = data.pixhawk_connected ? 'ONLINE' : 'OFFLINE';
    pixEl.style.color = data.pixhawk_connected ? '#4ade80' : '#f87171';

    const fixEl = document.getElementById('pop-gps-fix');
    fixEl.textContent = data.gps_fix ? '3D Fix' : 'Geen fix';
    fixEl.style.color = data.gps_fix ? '#4ade80' : '#f87171';

    document.getElementById('pop-gps-sats').textContent = data.gps_satellites || '--';

    // Lat/Lon alleen tonen bij echte fix (niet 0,0)
    if (data.gps_fix && data.gps_lat && data.gps_lon) {
        document.getElementById('pop-gps-lat').textContent = data.gps_lat.toFixed(7) + '°';
        document.getElementById('pop-gps-lon').textContent = data.gps_lon.toFixed(7) + '°';
        document.getElementById('pop-gps-alt').textContent = (data.altitude || 0).toFixed(1) + ' m';
    } else {
        document.getElementById('pop-gps-lat').textContent = '--';
        document.getElementById('pop-gps-lon').textContent = '--';
        document.getElementById('pop-gps-alt').textContent = '--';
    }
}

/**
 * Update de WiFi (Pi ↔ Grondstation) quick-card.
 * Toont RSSI + aantal verbonden clients.
 */
function updateWifiQuickCard(data) {
    const bars = data.wifi_connected ? window.rssiToBars(data.wifi_rssi) : 0;
    window.updateBars('wifi-bars', bars);

    document.getElementById('quick-wifi-rssi').textContent =
        data.wifi_connected ? data.wifi_rssi + ' dBm' : 'N/A';

    const lab = data.wifi_connected
        ? window.rssiLabel(data.wifi_rssi)
        : { text: 'Geen verbinding', cls: 'label-bad' };

    const labelEl = document.getElementById('quick-wifi-label');
    labelEl.textContent = lab.text +
        (data.wifi_connected ? ' · ' + data.wifi_clients + ' client(s)' : '');
    labelEl.className = 'quick-sub ' + lab.cls;

    window.setCardStatus('quick-wifi', window.barsToStatusLevel(bars, data.wifi_connected));
}

/**
 * Update de SiK telemetrie quick-card.
 *
 * RSSI is het minimum van local en remote (worst-of-both) zodat
 * een zwakke kant niet verborgen wordt door een sterke kant.
 */
function updateTelemQuickCard(data) {
    const rssi = Math.min(
        data.telem_rssi_local  || -100,
        data.telem_rssi_remote || -100
    );
    const bars = data.telem_connected ? window.rssiToBars(rssi) : 0;
    window.updateBars('telem-bars', bars);

    document.getElementById('quick-telem-rssi').textContent =
        data.telem_connected ? rssi.toFixed(0) + ' dBm' : 'N/A';

    const lab = data.telem_connected
        ? window.rssiLabel(rssi)
        : { text: 'Geen data', cls: 'label-bad' };

    const labelEl = document.getElementById('quick-telem-label');
    labelEl.textContent = lab.text;
    labelEl.className   = 'quick-sub ' + lab.cls;

    window.setCardStatus('quick-telem', window.barsToStatusLevel(bars, data.telem_connected));
}

// Expose op window
window.registerCoreSocketHandlers = registerCoreSocketHandlers;
