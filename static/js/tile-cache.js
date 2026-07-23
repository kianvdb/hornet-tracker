/**
 * TILE CACHE — offline kaart-tiles beheer vanuit het dashboard
 *
 * Bevat:
 *  - openTileCacheModal()    open modal + laad huidige stats
 *  - useCurrentDronePos()    vul lat/lon velden met huidige drone GPS
 *  - startPrefetch()         POST naar /api/tiles/prefetch + poll status
 *  - clearAllTiles()         DELETE /api/tiles (met bevestiging)
 *  - deleteOneSource(src)    DELETE /api/tiles?source=X
 *
 * Backend REST endpoints:
 *  GET    /api/tiles/stats              — hoeveel tiles per source
 *  POST   /api/tiles/prefetch           — start background prefetch
 *  GET    /api/tiles/prefetch/status    — pollen tijdens prefetch
 *  DELETE /api/tiles                    — wis alles (of ?source=X voor één)
 */

/** Interval timer voor status-polling tijdens prefetch */
let prefetchPollTimer = null;

/** Labels per source (voor display in stats) */
const SOURCE_LABELS = {
    osm: '🗺️ Stratenplan',
    sat: '🛰️ Satelliet',
    hyb: '🏷️ Straatnamen'
};


/**
 * Open de cache-modal en laad direct de huidige stats.
 */
function openTileCacheModal() {
    document.getElementById('tile-cache-modal').classList.add('show');
    refreshTileStats();
    resetLocationDisplay();
    document.getElementById('prefetch-address').value = '';
    initTileCacheAutocomplete();
}

/**
 * Haal stats op van de Pi en render de stats-sectie van de modal.
 */
async function refreshTileStats() {
    try {
        const response = await fetch('/api/tiles/stats');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        renderTileStats(data);
    } catch (err) {
        console.error('[tile-cache] stats ophalen mislukt:', err);
        document.getElementById('tile-stats').innerHTML =
            '<div class="tile-stats-row" style="color:#f87171;">Stats ophalen mislukt</div>';
    }
}

/**
 * Render de stats-sectie. Toont per source aantal tiles + MB, plus
 * een totaal-regel onderaan.
 */
function renderTileStats(data) {
    const container = document.getElementById('tile-stats');
    const sources = data.sources || {};

    let html = '';
    let hasAny = false;

    for (const src of ['osm', 'sat', 'hyb']) {
        const s = sources[src] || { count: 0, bytes: 0 };
        const label = SOURCE_LABELS[src] || src;
        const mb = (s.bytes / 1024 / 1024).toFixed(1);
        const deleteBtn = s.count > 0
            ? `<button class="tile-stats-delete" onclick="deleteOneSource('${src}')">Wissen</button>`
            : '';
        html += `
            <div class="tile-stats-row">
                <span class="tile-stats-source">${label}</span>
                <span>
                    <span class="tile-stats-value">${s.count} tiles · ${mb} MB</span>
                    ${deleteBtn}
                </span>
            </div>`;
        if (s.count > 0) hasAny = true;
    }

    const totalMb = (data.total_bytes / 1024 / 1024).toFixed(1);
    html += `
        <div class="tile-stats-row total">
            <span>Totaal</span>
            <span class="tile-stats-value">${data.total_count} tiles · ${totalMb} MB</span>
        </div>`;

    if (!hasAny) {
        html = '<div class="tile-stats-row" style="color:#aaa; font-style:italic;">' +
               'Geen offline tiles opgeslagen — download een gebied hieronder' +
               '</div>' + html;
    }

    container.innerHTML = html;
}

/**
 * Vul lat/lon velden met de huidige drone-positie (vereist GPS-fix).
 */
function useCurrentDronePos() {
    // Drone-positie uit de gedeelde status i.p.v. de kaart-info-tekst: die
    // lat/lon-velden zijn uit de UI verwijderd, de status-dict is de bron.
    const status = window.lastKnownStatus || {};
    const lat = status.gps_lat;
    const lon = status.gps_lon;

    if (!window.hasFix() || typeof lat !== 'number' || typeof lon !== 'number' || (lat === 0 && lon === 0)) {
        window.showToast('Geen GPS positie beschikbaar');
        return;
    }

    document.getElementById('prefetch-lat').value = lat.toFixed(4);
    document.getElementById('prefetch-lon').value = lon.toFixed(4);
    document.getElementById('prefetch-address').value = '';
    setLocationDisplay('Huidige drone-positie', lat, lon);
}

/**
 * Verzamel form-waarden, valideer, POST naar backend.
 * Start dan polling om de progress-bar te updaten.
 */
async function startPrefetch() {
    const lat = parseFloat(document.getElementById('prefetch-lat').value);
    const lon = parseFloat(document.getElementById('prefetch-lon').value);
    const radius = parseFloat(document.getElementById('prefetch-radius').value);
    const zoomMin = parseInt(document.getElementById('prefetch-zoom-min').value, 10);
    const zoomMax = parseInt(document.getElementById('prefetch-zoom-max').value, 10);

    const sources = [];
    if (document.getElementById('prefetch-src-osm').checked) sources.push('osm');
    if (document.getElementById('prefetch-src-sat').checked) sources.push('sat');
    if (document.getElementById('prefetch-src-hyb').checked) sources.push('hyb');

    // Validatie
    if (isNaN(lat) || isNaN(lon)) {
        window.showToast('Vul lat/lon in');
        return;
    }
    if (isNaN(radius) || radius <= 0 || radius > 50) {
        window.showToast('Radius moet tussen 0 en 50 km zijn');
        return;
    }
    if (zoomMin > zoomMax) {
        window.showToast('Zoom min moet kleiner of gelijk aan zoom max zijn');
        return;
    }
    if (sources.length === 0) {
        window.showToast('Selecteer minimaal één bron');
        return;
    }

    // Disable start-knop tijdens prefetch
    document.getElementById('prefetch-start-btn').disabled = true;

    try {
        const response = await fetch('/api/tiles/prefetch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                lat: lat, lon: lon,
                radius_km: radius,
                zoom_min: zoomMin, zoom_max: zoomMax,
                sources: sources
            })
        });

        if (response.status === 409) {
            window.showToast('Er loopt al een prefetch — even wachten');
            document.getElementById('prefetch-start-btn').disabled = false;
            return;
        }
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        // Start polling
        document.getElementById('prefetch-progress').style.display = 'block';
        document.getElementById('prefetch-bar-fill').style.width = '0%';
        document.getElementById('prefetch-label').textContent = 'Voorbereiden...';

        startStatusPolling();

    } catch (err) {
        console.error('[tile-cache] prefetch starten mislukt:', err);
        window.showToast('Prefetch starten mislukt: ' + err.message);
        document.getElementById('prefetch-start-btn').disabled = false;
    }
}

/**
 * Poll de prefetch-status elke 500ms. Stop bij voltooid + ververs stats.
 */
function startStatusPolling() {
    if (prefetchPollTimer) clearInterval(prefetchPollTimer);

    prefetchPollTimer = setInterval(async () => {
        try {
            const response = await fetch('/api/tiles/prefetch/status');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const state = await response.json();

            updateProgressDisplay(state);

            if (!state.running) {
                // Klaar — stop polling, ververs stats, enable knop
                clearInterval(prefetchPollTimer);
                prefetchPollTimer = null;
                document.getElementById('prefetch-start-btn').disabled = false;
                refreshTileStats();
                window.showToast(state.message || 'Prefetch klaar');
            }
        } catch (err) {
            console.error('[tile-cache] status polling fout:', err);
            clearInterval(prefetchPollTimer);
            prefetchPollTimer = null;
            document.getElementById('prefetch-start-btn').disabled = false;
        }
    }, 500);
}

/**
 * Update de visuele progress-bar + label op basis van state.
 */
function updateProgressDisplay(state) {
    const pct = state.total > 0 ? Math.round(100 * state.done / state.total) : 0;
    document.getElementById('prefetch-bar-fill').style.width = pct + '%';
    document.getElementById('prefetch-label').textContent =
        `${state.done} / ${state.total} (${state.success} OK, ${state.fail} fail)`;
}

/**
 * Wis één source na bevestiging.
 */
async function deleteOneSource(source) {
    const label = SOURCE_LABELS[source] || source;
    if (!confirm(`Alle ${label} tiles wissen?`)) return;

    try {
        const response = await fetch(`/api/tiles?source=${encodeURIComponent(source)}`, {
            method: 'DELETE'
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        window.showToast(`${data.deleted_count} tiles gewist (${(data.deleted_bytes/1024/1024).toFixed(1)} MB)`);
        refreshTileStats();
    } catch (err) {
        console.error('[tile-cache] delete mislukt:', err);
        window.showToast('Wissen mislukt');
    }
}

/**
 * Wis alle tiles na bevestiging.
 */
async function clearAllTiles() {
    if (!confirm('Alle offline tiles wissen?\n\nJe zult ze opnieuw moeten downloaden voor offline gebruik.')) {
        return;
    }

    try {
        const response = await fetch('/api/tiles', { method: 'DELETE' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        window.showToast(`${data.deleted_count} tiles gewist (${(data.deleted_bytes/1024/1024).toFixed(1)} MB)`);
        refreshTileStats();
    } catch (err) {
        console.error('[tile-cache] clear all mislukt:', err);
        window.showToast('Wissen mislukt');
    }
}
// ============================================
// ADRES-AUTOCOMPLETE — gedelegeerd aan nominatim.js
// ============================================

let tileCacheAutocompleteInit = false;

/**
 * Lazy-initialize de autocomplete bij eerste modal-open. Gedaan via
 * setupAddressAutocomplete uit nominatim.js zodat de adres-zoek-logica
 * gedeeld is met de map-card adres-zoek.
 */
function initTileCacheAutocomplete() {
    if (tileCacheAutocompleteInit) return;
    const inputEl = document.getElementById('prefetch-address');
    const sugEl   = document.getElementById('address-suggestions');
    if (!inputEl || !sugEl) return;

    window.setupAddressAutocomplete({
        inputEl: inputEl,
        suggestionEl: sugEl,
        onSelect: function(lat, lon, name) {
            document.getElementById('prefetch-lat').value = lat.toFixed(4);
            document.getElementById('prefetch-lon').value = lon.toFixed(4);
            inputEl.value = name;
            setLocationDisplay(name, lat, lon);
        }
    });
    tileCacheAutocompleteInit = true;
}

/**
 * Wordt op de input nog steeds aangeroepen via oninput="onAddressInput()"
 * vanuit de HTML. We laten dat staan en zorgen dat de autocomplete bij
 * eerste typen klaar is.
 */
function onAddressInput() {
    initTileCacheAutocomplete();
    // De autocomplete listener uit setupAddressAutocomplete handelt het
    // verder af. Deze stub blijft bestaan zodat het HTML-attribuut werkt.
}



/**
 * Update de visuele locatie-bevestiging onder het adresveld.
 * Toont welke locatie is geselecteerd voordat operator op "Download" klikt.
 */
function setLocationDisplay(name, lat, lon) {
    // Bouw via DOM-API zodat we geen escape-helper nodig hebben
    const el = document.getElementById('prefetch-location');
    el.textContent = name + ' ';
    const coordSpan = document.createElement('span');
    coordSpan.style.color = '#888';
    coordSpan.style.fontSize = '0.85em';
    coordSpan.textContent = `(${lat.toFixed(4)}, ${lon.toFixed(4)})`;
    el.appendChild(coordSpan);
    el.classList.add('set');
}

/**
 * Reset locatie-display bij modal open zodat we niet een stale locatie tonen.
 */
function resetLocationDisplay() {
    const el = document.getElementById('prefetch-location');
    el.textContent = 'Geen locatie geselecteerd';
    el.classList.remove('set');
}

// Window exports
window.openTileCacheModal  = openTileCacheModal;
window.refreshTileStats    = refreshTileStats;
window.useCurrentDronePos  = useCurrentDronePos;
window.startPrefetch       = startPrefetch;
window.deleteOneSource     = deleteOneSource;
window.clearAllTiles       = clearAllTiles;
window.onAddressInput = onAddressInput;