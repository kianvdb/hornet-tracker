/**
 * MAP — Leaflet kaart met drone tracking
 *
 * Bevat:
 *  - initMap()                 init bij page-load op fallback locatie (Brussel)
 *  - updateMap(lat, lon, alt)  update drone marker positie + trail
 *  - centerMap()               centreer kaart op huidige drone positie
 *  - toggleTrail()             trail aan/uit zetten
 *  - getCurrentMap()           geef Leaflet map instance terug (voor coord-log)
 *  - getCurrentTrailCount()    aantal trail punten (voor debugging/info)
 *
 * Strategie:
 *  - Kaart laadt direct bij page-load op fallback locatie (uitgezoomd op
 *    Brussel/Belgie). Operator ziet altijd een kaart, ook zonder GPS-fix.
 *  - Bij eerste echte GPS-fix: zoom in op drone positie, plaats marker,
 *    verberg "wachten op fix" badge.
 *  - Trail accumuleert pas vanaf eerste echte fix (geen (0,0) punten).
 *
 * Afhankelijkheden:
 *  - Leaflet (geladen via CDN in dashboard.html)
 *  - HTML elementen: #map, #map-lat, #map-lon, #map-alt, #gps-waiting-badge
 */

// ============================================
// CONFIGURATIE
// ============================================

/** Fallback positie als geen GPS-fix beschikbaar is (Brussel Grote Markt) */
const FALLBACK_LAT  = 50.8503;
const FALLBACK_LON  = 4.3517;
const FALLBACK_ZOOM = 9;        // Uitgezoomd: bijna heel Belgie zichtbaar

/** Zoom level na eerste echte GPS-fix */
const FIX_ZOOM = 18;

/** Max aantal trail punten (oudste wordt verwijderd) */
const MAX_TRAIL_POINTS = 1000;

// ============================================
// STATE
// ============================================

let map = null;                 // Leaflet map instance
let droneMarker = null;         // Drone positie marker
let trailLine = null;           // Polyline met afgelegde route
let trailCoords = [];           // Array van [lat, lon] paren
let showTrail = true;
let hasGpsFix = false;          // false totdat eerste echte fix binnenkomt

// ============================================
// INITIALISATIE
// ============================================

/**
 * Initialiseer Leaflet kaart bij page-load.
 * Wordt aangeroepen vanuit main.js na DOMContentLoaded.
 */
function initMap() {
    map = L.map('map', {
        center: [FALLBACK_LAT, FALLBACK_LON],
        zoom: FALLBACK_ZOOM,
        zoomControl: true
    });

    // --- Kaartlagen ---
    const osmLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© OpenStreetMap',
        maxZoom: 22
    });

    const satelliteLayer = L.tileLayer(
        'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
        attribution: '© Esri',
        maxZoom: 22
    });

    const hybridLayer = L.tileLayer(
        'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}', {
        maxZoom: 22
    });

    // Start met satelliet + straatnamen overlay
    satelliteLayer.addTo(map);
    hybridLayer.addTo(map);

    // Layer control rechtsboven
    const baseMaps = {
        "🛰️ Satelliet": satelliteLayer,
        "🗺️ Stratenplan": osmLayer
    };
    const overlays = {
        "Straatnamen": hybridLayer
    };
    L.control.layers(baseMaps, overlays, { position: 'topright' }).addTo(map);

    // Lege trail polyline (gevuld zodra fix binnenkomt)
    trailLine = L.polyline([], { color: '#f5a623', weight: 3, opacity: 0.7 }).addTo(map);


    // Klik op kaart = manual pin flow (delegeert naar coord-log.js)
    map.on('click', function(e) {
        window.handleMapClick(e.latlng);
    });

    console.log('[map] Geinitialiseerd op fallback locatie (Brussel)');

}

/**
 * Update drone positie. Wordt bij elke status_update van de server aangeroepen.
 * Negeer (0,0) coords — dat is "geen fix".
 */
function updateMap(lat, lon, alt) {
    // Skip lege/ongeldige coords
    if ((lat === 0 && lon === 0) || lat === null || lon === null) {
        return;
    }

    // Eerste echte fix: zoom in, plaats marker, verberg badge
    if (!hasGpsFix) {
        hasGpsFix = true;

        const droneIcon = L.divIcon({
            html: '<div style="font-size:28px;filter:drop-shadow(0 2px 4px rgba(0,0,0,0.5));">🚁</div>',
            className: '',
            iconSize: [28, 28],
            iconAnchor: [14, 14]
        });

        droneMarker = L.marker([lat, lon], { icon: droneIcon }).addTo(map);
        map.setView([lat, lon], FIX_ZOOM);

        // Verberg "wachten op fix" badge
        const badge = document.getElementById('gps-waiting-badge');
        if (badge) badge.style.display = 'none';

        console.log(`[map] Eerste GPS-fix: ${lat.toFixed(7)}, ${lon.toFixed(7)}`);
    } else {
        droneMarker.setLatLng([lat, lon]);
    }

    // Trail bijwerken
    if (showTrail) {
        trailCoords.push([lat, lon]);
        if (trailCoords.length > MAX_TRAIL_POINTS) {
            trailCoords.shift();
        }
        trailLine.setLatLngs(trailCoords);
    }

    // Info-regel onder de kaart
    document.getElementById('map-lat').textContent = lat.toFixed(7);
    document.getElementById('map-lon').textContent = lon.toFixed(7);
    document.getElementById('map-alt').textContent = alt.toFixed(1);
}

/** Centreer kaart op huidige drone positie (alleen als er een fix is) */
function centerMap() {
    if (hasGpsFix && droneMarker) {
        map.setView(droneMarker.getLatLng(), map.getZoom());
    } else {
        window.showToast('Geen GPS positie om naar te centreren');
    }
}

/** Trail aan/uit schakelen. Bij uitschakelen wordt huidige trail gewist. */
function toggleTrail() {
    showTrail = !showTrail;
    if (!showTrail) {
        trailCoords = [];
        if (trailLine) trailLine.setLatLngs([]);
    }
    window.showToast(showTrail ? 'Trail ingeschakeld' : 'Trail uitgeschakeld');
}

// ============================================
// ACCESSORS (voor andere modules)
// ============================================

/** Geef Leaflet map instance terug — gebruikt door coord-log.js voor pins */
function getCurrentMap() {
    return map;
}

/** Of er een geldige GPS-fix is — gebruikt door coord-log.js */
function hasFix() {
    return hasGpsFix;
}

// Expose op window
window.initMap         = initMap;
window.updateMap       = updateMap;
window.centerMap       = centerMap;
window.toggleTrail     = toggleTrail;
window.getCurrentMap   = getCurrentMap;
window.hasFix          = hasFix;
