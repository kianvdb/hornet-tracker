/**
 * COORD-LOG — gelogde coordinaten met pins op kaart
 *
 * Bevat:
 *  - logCoordinate()              start log-flow voor huidige drone-positie (auto)
 *  - handleMapClick(latlng)       start log-flow voor manueel geprikte locatie
 *  - openLogModal(data)           open de log-modal met voor-gevulde defaults
 *  - confirmLogEntry()            opslaan vanuit modal (status + notitie)
 *  - updateLogDisplay()           render lijst rechts van de kaart
 *  - copyCoord(index)             kopieer "lat, lon" naar clipboard
 *  - clearLog()                   wis alle gelogde punten
 *  - exportLog()                  download CSV
 *
 * Entry-structuur:
 *   {
 *     lat, lon, alt,
 *     time, date,                    // tijdstempel
 *     source: 'drone' | 'manueel',   // hoe is deze pin ontstaan
 *     status: '' | 'gemeld' | ...    // operator-toegekende status
 *     notes: ''                      // vrije tekst
 *   }
 */

/** Array met alle gelogde punten */
let coordLog = [];

/** Pending entry tijdens modal-flow (wachten op operator confirm) */
let pendingEntry = null;
/** Index van entry die bewerkt wordt (null bij nieuwe entry) */
let editingIndex = null;
/** Houdt geplaatste markers bij zodat we ze later kunnen verwijderen */
let logMarkers = [];
/** Storage key voor localStorage persistence */
const STORAGE_KEY = 'hornet-tracker-coordlog-v1';

/**
 * Laad gelogde coördinaten uit localStorage bij page-init.
 * Roep aan vanuit main.js bij DOMContentLoaded, voor updateLogDisplay().
 */
function loadCoordLogFromStorage() {
    try {
        const stored = localStorage.getItem(STORAGE_KEY);
        if (stored) {
            const parsed = JSON.parse(stored);
            if (Array.isArray(parsed)) {
                coordLog = parsed;
                console.log(`[coord-log] ${coordLog.length} entries geladen uit localStorage`);
                // Plaats markers terug op de kaart
                coordLog.forEach((entry, idx) => placeMarker(entry, idx + 1));
            }
        }
    } catch (err) {
        console.warn('[coord-log] kon localStorage niet lezen:', err);
    }
}

/**
 * Persisteer huidige log naar localStorage.
 * Wordt aangeroepen na elke add/clear operatie.
 */
function saveCoordLogToStorage() {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(coordLog));
    } catch (err) {
        console.warn('[coord-log] kon niet opslaan naar localStorage:', err);
    }
}

/**
 * Auto-log: gebruik huidige drone-positie. Triggered door 'Log Positie' knop.
 * Vereist een actieve GPS-fix.
 */
function logCoordinate() {
    const lat = parseFloat(document.getElementById('map-lat').textContent);
    const lon = parseFloat(document.getElementById('map-lon').textContent);
    const alt = parseFloat(document.getElementById('map-alt').textContent);

    if (!window.hasFix() || isNaN(lat) || isNaN(lon) || (lat === 0 && lon === 0)) {
        window.showToast('Geen GPS positie beschikbaar');
        return;
    }

    openLogModal({
        lat: lat,
        lon: lon,
        alt: isNaN(alt) ? 0 : alt,
        source: 'drone',
        defaultStatus: 'wordt_onderzocht'
    });
}

/**
 * Manual log: triggered door klik op de kaart. Hoogte is onbekend (operator
 * pint vanaf de grond, niet vanuit drone-perspectief) — vullen we als 0 in.
 */
function handleMapClick(latlng) {
    openLogModal({
        lat: latlng.lat,
        lon: latlng.lng,
        alt: 0,
        source: 'manueel',
        defaultStatus: 'gemeld'
    });
}

/**
 * Open log-modal met bestaande entry voor bewerken.
 * Verschilt van openLogModal in dat editingIndex wordt gezet zodat
 * confirmLogEntry weet dat het een update is, geen toevoeging.
 */
function editCoord(index) {
    const entry = coordLog[index];
    if (!entry) return;

    editingIndex = index;

    // Hergebruik dezelfde modal-velden als bij nieuwe entry
    pendingEntry = {
        lat: entry.lat,
        lon: entry.lon,
        alt: entry.alt,
        source: entry.source,
        // defaultStatus wordt straks overschreven door de huidige status
        defaultStatus: entry.status
    };

    // Titel + source-tekst aanpassen voor edit-context
    const title  = document.getElementById('log-modal-title');
    const source = document.getElementById('log-modal-source');
    title.textContent  = '✏️ Entry bewerken (#' + (index + 1) + ')';
    source.textContent = entry.source === 'drone'
        ? 'Oorspronkelijk geplaatst op drone-positie'
        : 'Oorspronkelijk handmatig geplaatst door operator';

    // Coordinaten preview (read-only)
    document.getElementById('log-modal-coords').textContent =
        entry.lat.toFixed(7) + ', ' + entry.lon.toFixed(7);
    document.getElementById('log-modal-alt').textContent = entry.alt.toFixed(1);

    // Vul huidige status en notitie in
    document.getElementById('log-modal-status').value = entry.status || '';
    document.getElementById('log-modal-notes').value  = entry.notes  || '';

    document.getElementById('log-modal').classList.add('show');
}

/**
 * Open de log-modal en vul defaults. Operator kiest status + notitie,
 * klikt opslaan → confirmLogEntry() persisteert.
 */
function openLogModal(data) {
    pendingEntry = data;

    // Titel + source-tekst
    const title  = document.getElementById('log-modal-title');
    const source = document.getElementById('log-modal-source');
    if (data.source === 'drone') {
        title.textContent  = '🚁 Drone-positie loggen';
        source.textContent = 'Pin geplaatst op huidige drone-positie';
    } else {
        title.textContent  = '📍 Locatie loggen';
        source.textContent = 'Pin handmatig geplaatst door operator';
    }

    // Coördinaten preview
    document.getElementById('log-modal-coords').textContent =
        data.lat.toFixed(7) + ', ' + data.lon.toFixed(7);
    document.getElementById('log-modal-alt').textContent = data.alt.toFixed(1);

    // Defaults
    document.getElementById('log-modal-status').value = data.defaultStatus || '';
    document.getElementById('log-modal-notes').value  = '';

    document.getElementById('log-modal').classList.add('show');
}

/**
 * Opslaan-knop in log-modal. Twee paden:
 *  - editingIndex !== null  → update bestaande entry, marker-popup verversen
 *  - editingIndex === null  → nieuwe entry toevoegen
 */
function confirmLogEntry() {
    if (!pendingEntry) return;

    const status = document.getElementById('log-modal-status').value;
    const notes  = document.getElementById('log-modal-notes').value.trim();

    if (editingIndex !== null) {
        // --- UPDATE bestaande entry ---
        const entry = coordLog[editingIndex];
        entry.status = status;
        entry.notes  = notes;
        // tijd/datum/lat/lon/source ongewijzigd — entry blijft historisch correct

        // Vervang marker-popup met nieuwe inhoud
        refreshMarkerPopup(editingIndex);

        editingIndex  = null;
        pendingEntry  = null;
        window.hideModals();
        updateLogDisplay();
        saveCoordLogToStorage();
        window.showToast(`✏️ Entry #${entry ? coordLog.indexOf(entry) + 1 : ''} bijgewerkt`);
        return;
    }

    // --- NIEUWE entry ---
    const now  = new Date();
    const time = now.toLocaleTimeString('nl-BE', {
        hour: '2-digit', minute: '2-digit', second: '2-digit'
    });

    const entry = {
        lat:    pendingEntry.lat,
        lon:    pendingEntry.lon,
        alt:    pendingEntry.alt,
        time:   time,
        date:   now.toISOString(),
        source: pendingEntry.source,
        status: status,
        notes:  notes
    };

    coordLog.push(entry);
    placeMarker(entry, coordLog.length);

    pendingEntry = null;
    window.hideModals();
    updateLogDisplay();
    saveCoordLogToStorage();
    window.showToast(`📌 Entry #${coordLog.length} opgeslagen`);
}

/**
 * Plaats een Leaflet marker met source-specifiek icoon en popup-info.
 */
function placeMarker(entry, index) {
    const map = window.getCurrentMap();
    const iconEmoji = entry.source === 'drone' ? '🚁' : '📍';
    const pinIcon = L.divIcon({
        html: `<div style="font-size:20px;">${iconEmoji}</div>`,
        className: '',
        iconSize: [20, 20],
        iconAnchor: [10, 20]
    });
    const marker = L.marker([entry.lat, entry.lon], { icon: pinIcon }).addTo(map);
logMarkers.push(marker);
    let popupHtml = `<b>#${index}</b> ${iconEmoji} ${entry.source}<br>` +
                    `${entry.lat.toFixed(7)}, ${entry.lon.toFixed(7)}<br>` +
                    `Alt: ${entry.alt.toFixed(1)}m · ${entry.time}`;
    if (entry.status) {
        popupHtml += `<br><b>Status:</b> ${entry.status.replace('_', ' ')}`;
    }
    if (entry.notes) {
        popupHtml += `<br><i>${escapeHtml(entry.notes)}</i>`;
    }
    marker.bindPopup(popupHtml);
}

/**
 * Update de popup-inhoud van een bestaande marker na een edit.
 * Marker zelf hoeft niet vervangen — alleen de popup wordt herschreven.
 */
function refreshMarkerPopup(index) {
    const marker = logMarkers[index];
    if (!marker) return;
    const entry = coordLog[index];
    const iconEmoji = entry.source === 'drone' ? '🚁' : '📍';

    let popupHtml = `<b>#${index + 1}</b> ${iconEmoji} ${entry.source}<br>` +
                    `${entry.lat.toFixed(7)}, ${entry.lon.toFixed(7)}<br>` +
                    `Alt: ${entry.alt.toFixed(1)}m · ${entry.time}`;
    if (entry.status) {
        popupHtml += `<br><b>Status:</b> ${entry.status.replace('_', ' ')}`;
    }
    if (entry.notes) {
        popupHtml += `<br><i>${escapeHtml(entry.notes)}</i>`;
    }
    marker.setPopupContent(popupHtml);
}

/**
 * Render de log-lijst. Nieuwste bovenaan. Source-icoon links, status-badge
 * inline naast tijd. Notitie als grijze italic onder de regel.
 */
function updateLogDisplay() {
    const container = document.getElementById('coord-log');
    document.getElementById('log-count').textContent = coordLog.length + ' punten';

    if (coordLog.length === 0) {
        container.innerHTML =
            '<div class="coord-empty">Klik "📌 Log Positie" of klik op de kaart om coördinaten op te slaan</div>';
        return;
    }

    let html = '';
    for (let i = coordLog.length - 1; i >= 0; i--) {
        const e = coordLog[i];
        const gmapsUrl = `https://www.google.com/maps?q=${e.lat},${e.lon}`;
        const sourceIcon = e.source === 'drone' ? '🚁' : '📍';

        let statusBadge = '';
        if (e.status) {
            statusBadge = `<span class="coord-status status-${e.status}">${e.status.replace('_', ' ')}</span>`;
        }

        html += `
        <div class="coord-entry">
            <div style="flex:1; min-width:0;">
                <div style="display:flex; align-items:center; gap:4px;">
                    <span class="coord-source">${sourceIcon}</span>
                    <span class="coord-time">#${i+1} ${e.time}</span>
                    ${statusBadge}
                </div>
                <div style="margin-top:3px;">
                    <span class="coord-value" onclick="copyCoord(${i})" title="Klik om te kopiëren">${e.lat.toFixed(7)}, ${e.lon.toFixed(7)}</span>
                    <span class="coord-alt"> · ${e.alt.toFixed(1)}m</span>
                </div>
                ${e.notes ? `<div class="coord-notes">${escapeHtml(e.notes)}</div>` : ''}
            </div>
        <div class="coord-actions">
                <button class="coord-btn" onclick="editCoord(${i})" title="Bewerken">✏️</button>
                <button class="coord-btn" onclick="copyCoord(${i})" title="Kopieer">📋</button>
                <a class="coord-btn" href="${gmapsUrl}" target="_blank" title="Open in Google Maps">🗺️</a>
            </div>
        </div>`;
    }
    container.innerHTML = html;
}

/**
 * Simpele HTML-escape voor notitie-content (voorkomt XSS bij eventuele
 * externe data-import later).
 */
function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

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

function fallbackCopy(text) {
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    window.showToast(`📋 Gekopieerd: ${text}`);
}

function clearLog() {
    if (coordLog.length === 0) return;
    if (confirm('Alle gelogde coördinaten wissen?\n\nDit verwijdert ze permanent (ook na refresh).')) {
        // Verwijder markers van de kaart
        const map = window.getCurrentMap();
        logMarkers.forEach(m => map.removeLayer(m));
        logMarkers = [];

        coordLog = [];
        saveCoordLogToStorage();
        updateLogDisplay();
        window.showToast('Log gewist');
    }
}

/**
 * CSV-export. Nieuwe kolommen: source, status, notes.
 */
function exportLog() {
    if (coordLog.length === 0) {
        window.showToast('Geen data om te exporteren');
        return;
    }

    let csv = 'nr,tijd,datum,bron,status,latitude,longitude,altitude_m,notitie,google_maps_link\n';
    coordLog.forEach((e, i) => {
        // Quotes om notitie zodat komma's in vrije tekst niet de CSV breken
        const safeNotes = '"' + (e.notes || '').replace(/"/g, '""') + '"';
        csv += `${i+1},${e.time},${e.date},${e.source},${e.status || ''},` +
               `${e.lat.toFixed(7)},${e.lon.toFixed(7)},${e.alt.toFixed(1)},` +
               `${safeNotes},https://www.google.com/maps?q=${e.lat},${e.lon}\n`;
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

/**
 * Annuleer de log-modal. Reset edit-state zodat een volgende open een
 * nieuwe entry-flow start in plaats van per ongeluk in edit-modus te staan.
 */
function cancelLogEntry() {
    editingIndex = null;
    pendingEntry = null;
    window.hideModals();
}

// Expose op window
window.logCoordinate    = logCoordinate;
window.handleMapClick   = handleMapClick;
window.openLogModal     = openLogModal;
window.confirmLogEntry  = confirmLogEntry;
window.updateLogDisplay = updateLogDisplay;
window.copyCoord        = copyCoord;
window.clearLog         = clearLog;
window.exportLog        = exportLog;
window.loadCoordLogFromStorage = loadCoordLogFromStorage;
window.editCoord       = editCoord;
window.cancelLogEntry  = cancelLogEntry;
