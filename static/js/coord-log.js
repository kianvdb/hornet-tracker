/**
 * COORD-LOG — gelogde coordinaten met pins op kaart
 *
 * Bevat:
 *  - logCoordinate()    log huidige drone positie (incl. hoogte + tijd)
 *  - updateLogDisplay() render lijst rechts van de kaart
 *  - copyCoord(index)   kopieer "lat, lon" naar clipboard
 *  - clearLog()         wis alle gelogde punten (met confirm)
 *  - exportLog()        download CSV met alle punten
 *
 * Afhankelijkheden:
 *  - window.getCurrentMap()  Leaflet map instance van map.js
 *  - window.hasFix()         of er een geldige GPS-fix is
 *  - window.showToast()      notificatie helper van utils.js
 *  - DOM: #map-lat #map-lon #map-alt #coord-log #log-count
 *
 * Pins worden direct op de Leaflet kaart geplaatst (geen aparte layer).
 */

/** Array met alle gelogde punten: {lat, lon, alt, time, date} */
let coordLog = [];

/**
 * Log de huidige drone positie. Leest uit de map-info regel die door
 * map.js wordt bijgehouden. Plaatst een pin-marker op de kaart.
 */
function logCoordinate() {
    const lat = parseFloat(document.getElementById('map-lat').textContent);
    const lon = parseFloat(document.getElementById('map-lon').textContent);
    const alt = parseFloat(document.getElementById('map-alt').textContent);

    // Geen GPS-fix beschikbaar
    if (!window.hasFix() || isNaN(lat) || isNaN(lon) || (lat === 0 && lon === 0)) {
        window.showToast('Geen GPS positie beschikbaar');
        return;
    }

    const now = new Date();
    const time = now.toLocaleTimeString('nl-BE', {
        hour: '2-digit', minute: '2-digit', second: '2-digit'
    });

    const entry = { lat, lon, alt, time, date: now.toISOString() };
    coordLog.push(entry);

    // Pin marker plaatsen op de kaart
    const map = window.getCurrentMap();
    const pinIcon = L.divIcon({
        html: `<div style="font-size:18px;">📌</div>`,
        className: '',
        iconSize: [18, 18],
        iconAnchor: [9, 18]
    });
    const marker = L.marker([lat, lon], { icon: pinIcon }).addTo(map);
    marker.bindPopup(
        `<b>#${coordLog.length}</b><br>` +
        `${lat.toFixed(7)}, ${lon.toFixed(7)}<br>` +
        `Alt: ${alt.toFixed(1)}m<br>${time}`
    );

    updateLogDisplay();
    window.showToast(`📌 Positie #${coordLog.length} gelogd`);
}

/**
 * Render alle gelogde punten in de coord-log container (rechts van kaart).
 * Nieuwste bovenaan. Leeg = placeholder tonen.
 */
function updateLogDisplay() {
    const container = document.getElementById('coord-log');
    document.getElementById('log-count').textContent = coordLog.length + ' punten';

    if (coordLog.length === 0) {
        container.innerHTML =
            '<div class="coord-empty">Klik "📌 Log Positie" om coördinaten op te slaan</div>';
        return;
    }

    let html = '';
    for (let i = coordLog.length - 1; i >= 0; i--) {
        const e = coordLog[i];
        const gmapsUrl = `https://www.google.com/maps?q=${e.lat},${e.lon}`;
        html += `
        <div class="coord-entry">
            <span class="coord-time">#${i+1} ${e.time}</span>
            <span class="coord-value" onclick="copyCoord(${i})" title="Klik om te kopiëren">${e.lat.toFixed(7)}, ${e.lon.toFixed(7)}</span>
            <span class="coord-alt">${e.alt.toFixed(1)}m</span>
            <div class="coord-actions">
                <button class="coord-btn" onclick="copyCoord(${i})" title="Kopieer">📋</button>
                <a class="coord-btn" href="${gmapsUrl}" target="_blank" title="Open in Google Maps">🗺️</a>
            </div>
        </div>`;
    }
    container.innerHTML = html;
}

/**
 * Kopieer "lat, lon" naar clipboard. Fallback voor browsers zonder
 * clipboard API (oude Chrome op Pi, sommige veldlaptops zonder HTTPS).
 */
function copyCoord(index) {
    const e = coordLog[index];
    const text = `${e.lat.toFixed(7)}, ${e.lon.toFixed(7)}`;

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text)
            .then(() => window.showToast(`📋 Gekopieerd: ${text}`))
            .catch(() => fallbackCopy(text));
    } else {
        fallbackCopy(text);
    }
}

/**
 * Fallback clipboard via verborgen textarea + execCommand.
 * Werkt op http:// en oudere browsers waar Clipboard API geblokkeerd is.
 */
function fallbackCopy(text) {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    window.showToast(`📋 Gekopieerd: ${text}`);
}

/** Wis alle gelogde punten na bevestiging. Pins op kaart blijven staan. */
function clearLog() {
    if (coordLog.length === 0) return;
    if (confirm('Alle gelogde coördinaten wissen?')) {
        coordLog = [];
        updateLogDisplay();
        window.showToast('Log gewist');
    }
}

/**
 * Download alle gelogde punten als CSV met Google Maps links.
 * Filename: hornet_tracker_log_YYYY-MM-DD.csv
 */
function exportLog() {
    if (coordLog.length === 0) {
        window.showToast('Geen data om te exporteren');
        return;
    }

    let csv = 'nr,tijd,datum,latitude,longitude,altitude_m,google_maps_link\n';
    coordLog.forEach((e, i) => {
        csv += `${i+1},${e.time},${e.date},${e.lat.toFixed(7)},${e.lon.toFixed(7)},` +
               `${e.alt.toFixed(1)},https://www.google.com/maps?q=${e.lat},${e.lon}\n`;
    });

    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `hornet_tracker_log_${new Date().toISOString().slice(0,10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    window.showToast('CSV geëxporteerd');
}

// Expose op window
window.logCoordinate   = logCoordinate;
window.updateLogDisplay = updateLogDisplay;
window.copyCoord       = copyCoord;
window.clearLog        = clearLog;
window.exportLog       = exportLog;
