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
    const lat = parseFloat(document.getElementById('map-lat').textContent);
    const lon = parseFloat(document.getElementById('map-lon').textContent);

    if (!window.hasFix() || isNaN(lat) || isNaN(lon) || (lat === 0 && lon === 0)) {
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
// ADRES-AUTOCOMPLETE (Nominatim)
// ============================================
//
// Nominatim is OpenStreetMap's gratis geocoder. Rate limit: 1 req/sec.
// We respecteren dat met een 400ms debounce — gebruiker moet stoppen
// met typen voordat we de call doen.

let addressSearchTimer = null;
let lastAddressQuery = '';

/**
 * Triggered op elke input-change in het adresveld. Debounce naar
 * Nominatim om server-policy te respecteren.
 */
function onAddressInput() {
    const input = document.getElementById('prefetch-address');
    const query = input.value.trim();

    // Reset timer bij elke toetsaanslag
    if (addressSearchTimer) clearTimeout(addressSearchTimer);

    // Verberg suggesties bij lege input
    if (query.length < 3) {
        hideAddressSuggestions();
        return;
    }

    // Skip als query niet wijzigde (focus zonder typen)
    if (query === lastAddressQuery) return;

    // Toon "zoeken..." state
    showAddressLoading();

    // Debounce: wacht 400ms na laatste toetsaanslag
    addressSearchTimer = setTimeout(() => {
        searchAddress(query);
    }, 400);
}

/**
 * Voer de geocoding-call uit naar Nominatim.
 * Filtert resultaten op België + buurlanden voor relevantie.
 */
async function searchAddress(query) {
    lastAddressQuery = query;

    // Nominatim URL met:
    //  - q: de zoekterm
    //  - format: json
    //  - limit: max 5 resultaten (voldoende voor dropdown)
    //  - countrycodes: be,nl,fr,lu,de (omringende landen)
    //  - accept-language: nl (Nederlandse adres-formattering bij voorkeur)
    const url = `https://nominatim.openstreetmap.org/search?` +
        `q=${encodeURIComponent(query)}&` +
        `format=json&limit=5&` +
        `countrycodes=be,nl,fr,lu,de&` +
        `accept-language=nl`;

    try {
        const response = await fetch(url, {
            headers: {
                // Nominatim policy vereist user-agent identificatie
                'Accept': 'application/json'
            }
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const results = await response.json();

        // Skip als de query intussen weer is gewijzigd (race-condition)
        const currentQuery = document.getElementById('prefetch-address').value.trim();
        if (currentQuery !== query) return;

        renderAddressSuggestions(results);

    } catch (err) {
        console.error('[geocode] zoeken mislukt:', err);
        document.getElementById('address-suggestions').innerHTML =
            '<div class="address-empty">Zoeken mislukt — geen internet?</div>';
    }
}

/**
 * Render suggestie-lijst onder het input-veld.
 */
function renderAddressSuggestions(results) {
    const container = document.getElementById('address-suggestions');

    if (!results || results.length === 0) {
        container.innerHTML = '<div class="address-empty">Geen resultaten</div>';
        container.classList.add('show');
        return;
    }

    let html = '';
    for (const r of results) {
        // display_name is vaak lang: "Inkendaal Ziekenhuis, Inkendaalstraat, ..., België"
        // Splits in hoofd + detail voor compact display
        const parts = r.display_name.split(',').map(s => s.trim());
        const mainName = parts[0] || r.display_name;
        const detailParts = parts.slice(1, 4); // beperkt tot 3 niveaus
        const detail = detailParts.join(', ');

        // Quote escapen voor onclick
        const lat = parseFloat(r.lat);
        const lon = parseFloat(r.lon);
        const safeName = mainName.replace(/'/g, "\\'");

        html += `
            <div class="address-suggestion" onclick="selectAddress(${lat}, ${lon}, '${safeName}')">
                <div class="addr-main">${escapeAddrHtml(mainName)}</div>
                <div class="addr-detail">${escapeAddrHtml(detail)}</div>
            </div>`;
    }
    container.innerHTML = html;
    container.classList.add('show');
}

/**
 * Wanneer een suggestie wordt geklikt: vul lat/lon en sluit dropdown.
 */
function selectAddress(lat, lon, name) {
    document.getElementById('prefetch-lat').value = lat.toFixed(4);
    document.getElementById('prefetch-lon').value = lon.toFixed(4);
    document.getElementById('prefetch-address').value = name;
    hideAddressSuggestions();
    setLocationDisplay(name, lat, lon);
}

function showAddressLoading() {
    const container = document.getElementById('address-suggestions');
    container.innerHTML = '<div class="address-loading">Zoeken...</div>';
    container.classList.add('show');
}

function hideAddressSuggestions() {
    const container = document.getElementById('address-suggestions');
    container.classList.remove('show');
    container.innerHTML = '';
}

/**
 * Simpele HTML escape voor adres-content.
 */
function escapeAddrHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// Sluit suggesties als je elders klikt
document.addEventListener('click', function(e) {
    const container = document.getElementById('address-search-container');
    const suggestions = document.getElementById('address-suggestions');
    if (!e.target.closest('.address-search-container')) {
        if (suggestions) suggestions.classList.remove('show');
    }
});

/**
 * Update de visuele locatie-bevestiging onder het adresveld.
 * Toont welke locatie is geselecteerd voordat operator op "Download" klikt.
 */
function setLocationDisplay(name, lat, lon) {
    const el = document.getElementById('prefetch-location');
    el.innerHTML = `${escapeAddrHtml(name)} <span style="color:#888; font-size:0.85em;">(${lat.toFixed(4)}, ${lon.toFixed(4)})</span>`;
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
