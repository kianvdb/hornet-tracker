/**
 * COORD-LOG — gelogde coordinaten met pins op kaart
 *
 * BACKEND-PERSISTENTIE versie: alle log-state leeft in JSON-bestand
 * op de Pi (data/coord-log.json). Browser is een view-laag die via
 * REST endpoints leest/schrijft:
 *
 *   GET    /api/log                  — alle entries
 *   POST   /api/log                  — nieuwe entry (server genereert id)
 *   PUT    /api/log/<id>             — bestaande entry bewerken
 *   DELETE /api/log/<id>             — één entry verwijderen
 *   DELETE /api/log                  — alle entries wissen
 *
 * Identificatie van entries gebeurt via stabiele server-side ID
 * (timestamp-hex), niet via array-index. Zo blijven PUT/DELETE robuust
 * tegen tussentijdse wijzigingen.
 *
 * Entry-structuur (zoals teruggegeven door backend):
 *   {
 *     id,                            // server-generated: "timestamp-hex"
 *     lat, lon, alt,
 *     time, date,                    // tijdstempels (client-side gegenereerd)
 *     source: 'drone' | 'manueel',
 *     status: '' | 'gemeld' | 'wordt_onderzocht' | 'waargenomen'
 *             | 'bestreden' | 'vals_alarm',
 *     notes: ''
 *   }
 */

/** Lokale cache van entries. Wordt geupdate na elke succesvolle server-call. */
let coordLog = [];

/** Pending entry tijdens modal-flow (wachten op operator confirm). */
let pendingEntry = null;

/** ID van entry die bewerkt wordt (null bij nieuwe entry). */
let editingId = null;

/**
 * Mapping van entry-id naar Leaflet marker zodat we markers kunnen
 * opruimen op basis van stabiele identifier i.p.v. positie in array.
 */
let markersById = {};


// ============================================
// SERVER COMMUNICATIE — REST helpers
// ============================================

/**
 * Haal alle entries op van de Pi. Bij netwerkfout: behoud huidige
 * coordLog (graceful degradation in plaats van leeg te lopen).
 */
async function fetchAllEntries() {
    try {
        const response = await fetch('/api/log');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return await response.json();
    } catch (err) {
        console.error('[coord-log] kon entries niet ophalen van Pi:', err);
        window.showToast('Verbinding met Pi mislukt — log toont mogelijk oude data');
        return null;
    }
}

/**
 * Voeg een nieuwe entry toe op de server. Returns de entry met
 * server-generated id, of null bij fout.
 */
async function postEntry(data) {
    try {
        const response = await fetch('/api/log', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return await response.json();
    } catch (err) {
        console.error('[coord-log] entry toevoegen mislukt:', err);
        window.showToast('Opslaan op Pi mislukt');
        return null;
    }
}

/**
 * Update een bestaande entry. Body bevat alleen de te wijzigen velden
 * (typisch status + notes). Returns de bijgewerkte entry of null.
 */
async function putEntry(id, changes) {
    try {
        const response = await fetch(`/api/log/${encodeURIComponent(id)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(changes)
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return await response.json();
    } catch (err) {
        console.error('[coord-log] entry bewerken mislukt:', err);
        window.showToast('Wijziging opslaan op Pi mislukt');
        return null;
    }
}

/**
 * Verwijder één entry op basis van id. Returns true bij succes.
 */
async function deleteEntry(id) {
    try {
        const response = await fetch(`/api/log/${encodeURIComponent(id)}`, {
            method: 'DELETE'
        });
        if (!response.ok && response.status !== 204) {
            throw new Error(`HTTP ${response.status}`);
        }
        return true;
    } catch (err) {
        console.error('[coord-log] entry verwijderen mislukt:', err);
        window.showToast('Verwijderen op Pi mislukt');
        return false;
    }
}

/**
 * Wis alle entries (server-side). Returns true bij succes.
 */
async function deleteAllEntries() {
    try {
        const response = await fetch('/api/log', { method: 'DELETE' });
        if (!response.ok && response.status !== 204) {
            throw new Error(`HTTP ${response.status}`);
        }
        return true;
    } catch (err) {
        console.error('[coord-log] alles wissen mislukt:', err);
        window.showToast('Wissen op Pi mislukt');
        return false;
    }
}


// ============================================
// INITIALISATIE — bij DOMContentLoaded
// ============================================

/**
 * Haal log op van Pi en plaats markers terug op de kaart.
 * Wordt aangeroepen vanuit main.js bij page-load.
 */
async function initCoordLogFromServer() {
    const entries = await fetchAllEntries();
    if (entries === null) {
        // Netwerkfout — laat lege state staan, frontend kan later opnieuw proberen
        updateLogDisplay();
        return;
    }

    coordLog = entries;
    console.log(`[coord-log] ${coordLog.length} entries geladen van Pi`);

    // Markers terugplaatsen
    coordLog.forEach((entry, idx) => placeMarker(entry, idx + 1));
    updateLogDisplay();
}


/**
 * Luister naar entries die de SERVER zelf aanmaakt — vandaag alleen de
 * beaconpositie die search.py na een zoekvlucht wegschrijft.
 *
 * Waarom via een socket-event en niet via de gewone POST-flow: de zoekvlucht
 * draait in een thread op de Pi en moet zijn resultaat kunnen vastleggen ook
 * als er op dat moment geen browser openstaat. De server schrijft de entry
 * dus zelf weg; dit is puur de live weergave ervan.
 *
 * De guard op bestaande id's voorkomt een dubbele pin als het event binnenkomt
 * terwijl initCoordLogFromServer() de entry ook al had opgehaald.
 */
function registerLogEntryHandler() {
    if (!window.socket) return;
    window.socket.on('log_entry_added', function(entry) {
        if (!entry || coordLog.some(e => e.id === entry.id)) return;

        coordLog.push(entry);
        placeMarker(entry, coordLog.length);
        updateLogDisplay();
        window.showToast(`📍 Beacon gevonden — entry #${coordLog.length} gelogd`);
    });
}


// ============================================
// LOG-FLOW START — auto pin of map-click
// ============================================

/**
 * Auto-log: gebruik huidige drone-positie. Triggered door 'Log Positie' knop.
 * Vereist een actieve GPS-fix.
 */
function logCoordinate() {
    // Drone-positie uit de gedeelde status i.p.v. de kaart-info-tekst: die
    // lat/lon-velden zijn uit de UI verwijderd, de status-dict is de bron.
    const status = window.lastKnownStatus || {};
    const lat = status.gps_lat;
    const lon = status.gps_lon;
    const alt = status.altitude;

    if (!window.hasFix() || typeof lat !== 'number' || typeof lon !== 'number' || (lat === 0 && lon === 0)) {
        window.showToast('Geen GPS positie beschikbaar');
        return;
    }

    openLogModal({
        lat: lat,
        lon: lon,
        alt: (typeof alt === 'number') ? alt : 0,
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
 * Open log-modal met bestaande entry voor bewerken. editingId wordt
 * gezet zodat confirmLogEntry weet dat het een update is.
 */
function editCoord(id) {
    const entry = coordLog.find(e => e.id === id);
    if (!entry) return;

    editingId = id;

    pendingEntry = {
        lat:    entry.lat,
        lon:    entry.lon,
        alt:    entry.alt,
        source: entry.source,
        defaultStatus: entry.status
    };

    const index = coordLog.indexOf(entry);
    const title  = document.getElementById('log-modal-title');
    const source = document.getElementById('log-modal-source');
    title.textContent  = '✏️ Entry bewerken (#' + (index + 1) + ')';
    source.textContent = entry.source === 'drone'
        ? 'Oorspronkelijk geplaatst op drone-positie'
        : 'Oorspronkelijk handmatig geplaatst door operator';

   document.getElementById('log-modal-coords').textContent =
        entry.lat.toFixed(7) + ', ' + entry.lon.toFixed(7);
    document.getElementById('log-modal-alt').textContent = entry.alt.toFixed(1);

    // Adres tonen als bekend (anders rij verbergen)
    const addrRow = document.getElementById('log-modal-address-row');
    const addrSpan = document.getElementById('log-modal-address');
    if (entry.address) {
        addrSpan.textContent = entry.address;
        addrRow.style.display = '';
    } else {
        addrRow.style.display = 'none';
    }
    document.getElementById('log-modal-status').value = entry.status || '';
    document.getElementById('log-modal-notes').value  = entry.notes  || '';

    document.getElementById('log-modal').classList.add('show');
}

/**
 * Open de log-modal voor een nieuwe entry. Pre-fill defaults.
 */
function openLogModal(data) {
    pendingEntry = data;
    editingId = null;

    const title  = document.getElementById('log-modal-title');
    const source = document.getElementById('log-modal-source');
    if (data.source === 'drone') {
        title.textContent  = '🚁 Drone-positie loggen';
        source.textContent = 'Pin geplaatst op huidige drone-positie';
    } else {
        title.textContent  = '📍 Locatie loggen';
        source.textContent = 'Pin handmatig geplaatst door operator';
    }

    document.getElementById('log-modal-coords').textContent =
        data.lat.toFixed(7) + ', ' + data.lon.toFixed(7);
    document.getElementById('log-modal-alt').textContent = data.alt.toFixed(1);

    // Nieuwe entry: adres komt pas na server-side reverse-geocode bij opslag
    document.getElementById('log-modal-address-row').style.display = 'none';

    document.getElementById('log-modal-status').value = data.defaultStatus || '';
    document.getElementById('log-modal-notes').value  = '';

    document.getElementById('log-modal').classList.add('show');
}

/**
 * Opslaan-knop in log-modal. Twee paden:
 *  - editingId !== null  → PUT bestaande entry, ververs marker-popup
 *  - editingId === null  → POST nieuwe entry, voeg toe + marker
 */
async function confirmLogEntry() {
    if (!pendingEntry) return;

    const status = document.getElementById('log-modal-status').value;
    const notes  = document.getElementById('log-modal-notes').value.trim();

    if (editingId !== null) {
        // --- UPDATE bestaande entry via PUT ---
        const updated = await putEntry(editingId, { status, notes });
        if (!updated) return;  // Toast al getoond door putEntry

        // Update lokale cache
        const idx = coordLog.findIndex(e => e.id === editingId);
        if (idx !== -1) {
            coordLog[idx] = updated;
            refreshMarkerPopup(updated.id, idx + 1);
        }

        const oldId = editingId;
        editingId    = null;
        pendingEntry = null;
        window.hideModals();
        updateLogDisplay();

        const displayNumber = (idx !== -1) ? (idx + 1) : '';
        window.showToast(`✏️ Entry #${displayNumber} bijgewerkt`);
        return;
    }

    // --- NIEUWE entry via POST ---
    const now  = new Date();
    const time = now.toLocaleTimeString('nl-BE', {
        hour: '2-digit', minute: '2-digit', second: '2-digit'
    });

    const payload = {
        lat:    pendingEntry.lat,
        lon:    pendingEntry.lon,
        alt:    pendingEntry.alt,
        time:   time,
        date:   now.toISOString(),
        source: pendingEntry.source,
        status: status,
        notes:  notes
    };

    const created = await postEntry(payload);
    if (!created) return;

    coordLog.push(created);
    placeMarker(created, coordLog.length);

    pendingEntry = null;
    window.hideModals();
    updateLogDisplay();
    window.showToast(`📌 Entry #${coordLog.length} opgeslagen`);
}


// ============================================
// MARKERS op de kaart
// ============================================

/**
 * Plaats een Leaflet marker met source-specifiek icoon en popup-info.
 * Marker wordt opgeslagen in markersById onder de stabiele entry-id.
 */
function placeMarker(entry, displayNumber) {
    const map = window.getCurrentMap();
    const iconEmoji = entry.source === 'drone' ? '🚁' : '📍';
    const pinIcon = L.divIcon({
        html: `<div style="font-size:20px;">${iconEmoji}</div>`,
        className: '',
        iconSize: [20, 20],
        iconAnchor: [10, 20]
    });
    const marker = L.marker([entry.lat, entry.lon], { icon: pinIcon }).addTo(map);
    markersById[entry.id] = marker;

    marker.bindPopup(buildPopupHtml(entry, displayNumber));
}

/**
 * Update de popup-inhoud van een bestaande marker na een edit.
 */
function refreshMarkerPopup(id, displayNumber) {
    const marker = markersById[id];
    if (!marker) return;
    const entry = coordLog.find(e => e.id === id);
    if (!entry) return;
    marker.setPopupContent(buildPopupHtml(entry, displayNumber));
}

/**
 * Bouw de HTML-string voor een marker-popup. Gedeeld tussen placeMarker
 * en refreshMarkerPopup zodat de stijl consistent blijft.
 */
function buildPopupHtml(entry, displayNumber) {
    const iconEmoji = entry.source === 'drone' ? '🚁' : '📍';
    let html = `<b>#${displayNumber}</b> ${iconEmoji} ${entry.source}<br>`;
    if (entry.address) {
        html += `<b>${escapeHtml(entry.address)}</b><br>`;
    }
    html += `${entry.lat.toFixed(7)}, ${entry.lon.toFixed(7)}<br>` +
            `Alt: ${entry.alt.toFixed(1)}m · ${entry.time}`;
    if (entry.status) {
        html += `<br><b>Status:</b> ${entry.status.replace('_', ' ')}`;
    }
    if (entry.notes) {
        html += `<br><i>${escapeHtml(entry.notes)}</i>`;
    }
    return html;
}


// ============================================
// LIJST-WEERGAVE rechts van de kaart
// ============================================

/**
 * Render de log-lijst. Nieuwste bovenaan. Source-icoon links, status-badge
 * inline naast tijd. Notitie als grijze italic onder de regel.
 *
 * Actie-knoppen gebruiken entry.id (niet array-index) als parameter zodat
 * delete + concurrent edit niet de verkeerde entry raken.
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

        // entry.id wordt als string doorgegeven aan onclick handlers — quoten met "
        const safeId = e.id.replace(/"/g, '\\"');

        html += `
        <div class="coord-entry">
            <div style="flex:1; min-width:0;">
                <div style="display:flex; align-items:center; gap:4px;">
                    <span class="coord-source">${sourceIcon}</span>
                    <span class="coord-time">#${i+1} ${e.time}</span>
                    ${statusBadge}
                </div>
                ${e.address ? `<div class="coord-address" title="Adres uit reverse-geocoding">📍 ${escapeHtml(e.address)}</div>` : ''}
                <div style="margin-top:3px;">
                    <span class="coord-value" onclick="copyCoord('${safeId}')" title="Klik om te kopiëren">${e.lat.toFixed(7)}, ${e.lon.toFixed(7)}</span>
                    <span class="coord-alt"> · ${e.alt.toFixed(1)}m</span>
                </div>
                ${e.notes ? `<div class="coord-notes">${escapeHtml(e.notes)}</div>` : ''}
            </div>
            <div class="coord-actions">
                <button class="coord-btn" onclick="editCoord('${safeId}')" title="Bewerken">✏️</button>
                <button class="coord-btn" onclick="copyCoord('${safeId}')" title="Kopieer">📋</button>
                <a class="coord-btn" href="${gmapsUrl}" target="_blank" title="Open in Google Maps">🗺️</a>
                <button class="coord-btn coord-btn-danger" onclick="deleteCoord('${safeId}')" title="Verwijderen">🗑️</button>
            </div>
        </div>`;
    }
    container.innerHTML = html;
}

/**
 * Simpele HTML-escape voor notitie-content (voorkomt XSS).
 */
function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}


// ============================================
// COPY / DELETE / CLEAR — acties op entries
// ============================================

function copyCoord(id) {
    const e = coordLog.find(entry => entry.id === id);
    if (!e) return;
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

/**
 * Verwijder één entry. DELETE op server, dan lokale cache + marker.
 */
async function deleteCoord(id) {
    const idx = coordLog.findIndex(e => e.id === id);
    if (idx === -1) return;
    if (!confirm(`Entry #${idx + 1} verwijderen?`)) return;

    const ok = await deleteEntry(id);
    if (!ok) return;

    // Verwijder marker
    const map = window.getCurrentMap();
    if (markersById[id]) {
        map.removeLayer(markersById[id]);
        delete markersById[id];
    }

    // Verwijder uit lokale cache
    coordLog.splice(idx, 1);

    updateLogDisplay();
    window.showToast('Entry verwijderd');
}

/**
 * Wis alle entries via DELETE /api/log. Ruim markers en lokale state op.
 */
async function clearLog() {
    if (coordLog.length === 0) return;
    if (!confirm('Alle gelogde coördinaten wissen?\n\nDit verwijdert ze permanent (server-side).')) return;

    const ok = await deleteAllEntries();
    if (!ok) return;

    const map = window.getCurrentMap();
    Object.values(markersById).forEach(m => map.removeLayer(m));
    markersById = {};

    coordLog = [];
    updateLogDisplay();
    window.showToast('Log gewist');
}


// ============================================
// EXCEL EXPORT — selectie-modal
// ============================================

function exportLog() {
    if (coordLog.length === 0) {
        window.showToast('Geen entries om te exporteren');
        return;
    }
    buildExportList();
    document.getElementById('export-modal').classList.add('show');
}

function buildExportList() {
    const container = document.getElementById('export-entries');
    document.getElementById('export-select-all').checked = false;

    let html = '';
    for (let i = 0; i < coordLog.length; i++) {
        const e = coordLog[i];
        const sourceIcon = e.source === 'drone' ? '🚁' : '📍';
        const statusLabel = e.status ? e.status.replace('_', ' ') : '—';

        html += `
        <label class="export-entry">
            <input type="checkbox" class="export-checkbox"
                   data-index="${i}" onchange="updateExportCounter()">
            <div class="export-entry-content">
                <div class="export-entry-line1">
                    <span>${sourceIcon}</span>
                    <span><strong>#${i+1}</strong> · ${e.time} · ${statusLabel}</span>
                </div>
                <div class="export-entry-line2">
                    ${e.lat.toFixed(7)}, ${e.lon.toFixed(7)} · ${e.alt.toFixed(1)}m
                </div>
                ${e.notes ? `<div class="export-entry-notes">${escapeHtml(e.notes)}</div>` : ''}
            </div>
        </label>`;
    }
    container.innerHTML = html;
    updateExportCounter();
}

function updateExportCounter() {
    const checkboxes = document.querySelectorAll('.export-checkbox');
    const checked = document.querySelectorAll('.export-checkbox:checked');
    const total = checkboxes.length;
    const selected = checked.length;

    document.getElementById('export-counter').textContent =
        `${selected} van ${total} geselecteerd`;
    document.getElementById('export-confirm-btn').disabled = (selected === 0);

    const selectAll = document.getElementById('export-select-all');
    selectAll.checked = (selected === total && total > 0);
}

function toggleSelectAllExport() {
    const checked = document.getElementById('export-select-all').checked;
    document.querySelectorAll('.export-checkbox').forEach(cb => {
        cb.checked = checked;
    });
    updateExportCounter();
}

async function confirmExport() {
    const indices = Array.from(document.querySelectorAll('.export-checkbox:checked'))
        .map(cb => parseInt(cb.dataset.index, 10));

    if (indices.length === 0) {
        window.showToast('Geen entries geselecteerd');
        return;
    }

    const selectedEntries = indices.map(i => coordLog[i]);
    const filename = buildExportFilename();

    try {
        const response = await fetch('/api/export-xlsx', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ entries: selectedEntries, filename: filename })
        });

        if (!response.ok) {
            throw new Error(`Server error: ${response.status}`);
        }

        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);

        window.hideModals();
        window.showToast(`📊 Excel met ${indices.length} entries gedownload`);

    } catch (err) {
        console.error('Export error:', err);
        window.showToast('Excel-export mislukt: ' + err.message);
    }
}

/**
 * Belgische datum-notatie voor bestandsnaam:
 *   vespatrack_log_20-05-2026_17h30.xlsx
 */
function buildExportFilename() {
    const now = new Date();
    const dd = String(now.getDate()).padStart(2, '0');
    const mm = String(now.getMonth() + 1).padStart(2, '0');
    const yyyy = now.getFullYear();
    const hh = String(now.getHours()).padStart(2, '0');
    const min = String(now.getMinutes()).padStart(2, '0');
    return `vespatrack_log_${dd}-${mm}-${yyyy}_${hh}h${min}.xlsx`;
}


// ============================================
// MODAL ANNULEREN — reset edit-state
// ============================================

function cancelLogEntry() {
    editingId    = null;
    pendingEntry = null;
    window.hideModals();
}


// ============================================
// WINDOW EXPORTS
// ============================================

window.initCoordLogFromServer = initCoordLogFromServer;
window.registerLogEntryHandler = registerLogEntryHandler;
window.logCoordinate          = logCoordinate;
window.handleMapClick         = handleMapClick;
window.openLogModal           = openLogModal;
window.confirmLogEntry        = confirmLogEntry;
window.updateLogDisplay       = updateLogDisplay;
window.copyCoord              = copyCoord;
window.clearLog               = clearLog;
window.exportLog              = exportLog;
window.editCoord              = editCoord;
window.deleteCoord            = deleteCoord;
window.cancelLogEntry         = cancelLogEntry;
window.buildExportList        = buildExportList;
window.updateExportCounter    = updateExportCounter;
window.toggleSelectAllExport  = toggleSelectAllExport;
window.confirmExport          = confirmExport;