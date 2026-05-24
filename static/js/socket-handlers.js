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
        // Pi is online — eventuele openstaande modals sluiten (na reboot reconnect)
        window.hideModals();
    });

    window.socket.on('disconnect', function() {
        // Pi offline gedetecteerd door browser. Wordt momenteel niet visueel
        // getoond (was vroeger #pi-status in de Verbindingen card).
        // In een volgende commit komt dit terug als status in de navbar.
        console.warn('[socket] Pi connection lost');
    });


 // ============================================
    // STATUS_UPDATE — dispatcher
    // ============================================
    window.socket.on('status_update', dispatchStatusUpdate);

 // ============================================
    // THERMAL_FRAME — apart event om payload klein te houden
    // ============================================
    window.socket.on('thermal_frame', window.handleThermalFrame);

    // ============================================
    // THERMAL_BASELINE_RESULT — feedback na set/clear actie
    // ============================================
    window.socket.on('thermal_baseline_result', window.handleThermalBaselineResult);
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

   

    // --- Map (drone positie + trail) ---
    window.updateMap(data.gps_lat || 0, data.gps_lon || 0, data.altitude || 0);

    // --- Drone status card (armed, mode, batterij, hoogte) ---
    // Verdwijnt grotendeels in Doel 1. Voor nu inline.
    updateDroneStatusCard(data);
    window.updateModeButtons(data.flight_mode);

    // --- Quick status cards bovenaan (batterij, wifi, SiK) ---
    // Verdwijnen volledig in Doel 1 (vervangen door navbar).
  
    updateBatteryNavbar(data);
    updateWifiNavbar(data);
    updateGpsNavbar(data);

}

/**
 * Update de armed-status badge in de Besturing card.
 *
 * Was vroeger onderdeel van de Drone Status card (commit 7 weggehaald).
 * Flight mode, batterij en hoogte worden niet meer in een card getoond —
 * die info zit in de navbar (batterij/hoogte) of in de Besturing-knoppen
 * highlight (flight mode).
 */
function updateDroneStatusCard(data) {
    const armedEl = document.getElementById('armed-status');
    if (armedEl) {
        armedEl.textContent = data.armed ? 'ARMED' : 'DISARMED';
        armedEl.className   = 'status-badge ' + (data.armed ? 'armed' : 'disarmed');
    }
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
     const satsEl = document.getElementById('map-sats');
    if (satsEl) satsEl.textContent = data.gps_satellites || '--';
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


// Expose op window
window.registerCoreSocketHandlers = registerCoreSocketHandlers;
