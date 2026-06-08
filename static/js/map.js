/**
 * MAP — Leaflet kaart met drone tracking
 *
 * Bevat:
 *  - initMap()                         init bij page-load op fallback locatie (Brussel)
 *  - updateMap(lat, lon, alt, heading) update drone marker positie + rotatie + trail
 *  - centerMap()                       centreer kaart op huidige drone positie
 *  - toggleTrail()                     trail aan/uit zetten
 *  - getCurrentMap()                   geef Leaflet map instance terug (voor coord-log)
 *
 * Strategie:
 *  - Kaart laadt direct bij page-load op fallback locatie (uitgezoomd op
 *    Brussel/Belgie). Operator ziet altijd een kaart, ook zonder GPS-fix.
 *  - Bij eerste echte GPS-fix: zoom in op drone positie, plaats marker,
 *    verberg "wachten op fix" badge.
 *  - Trail accumuleert pas vanaf eerste echte fix (geen (0,0) punten).
 *  - Drone marker is een SVG-pijl die roteert op basis van magnetometer-heading
 *    uit MAVLink GLOBAL_POSITION_INT.hdg. Pijl-kop wijst in vlieg-richting.
 *
 * Afhankelijkheden:
 *  - Leaflet (vendored lokaal in static/vendor/leaflet/)
 *  - Tile cache via /tiles/<source>/{z}/{x}/{y}.png (Flask route)
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
// DRONE MARKER — SVG arrow
// ============================================

/**
 * Bouw de HTML/SVG voor de drone marker. Pijl-kop wijst naar boven (0°)
 * in de SVG; CSS-rotation past hem aan naar de actuele heading.
 *
 * Vormgeving:
 *  - Driehoekige kop in dashboard-amber (#f5a623)
 *  - Smalle rechthoekige staart eronder
 *  - Witte contour voor leesbaarheid op zowel satelliet- als straat-kaart
 *  - Drop-shadow zodat marker leesbaar blijft tegen lichte achtergronden
 *
 * @param {number} heading  graden 0-359 (0 = noord, 90 = oost)
 */
function buildDroneIconHtml(heading) {
    return `
        <div class="drone-marker" style="transform: rotate(${heading}deg);">
            <svg viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
                <!-- Paper airplane: pijl met v-inkeping achterkant -->
                <polygon points="20,2 38,36 20,28 2,36"
                         fill="#f5a623"
                         stroke="#ffffff"
                         stroke-width="1.5"
                         stroke-linejoin="round"/>
                <!-- Donkere midden-vouw voor 3D-effect -->
                <polygon points="20,2 20,28 2,36"
                         fill="#c8841a"
                         opacity="0.6"/>
            </svg>
        </div>
    `;
}

/**
 * Maak een Leaflet divIcon voor de drone met gegeven heading.
 */
function createDroneIcon(heading) {
    return L.divIcon({
        html: buildDroneIconHtml(heading),
        className: 'drone-marker-icon',  // geen Leaflet-default styling
        iconSize: [32, 40],
        iconAnchor: [16, 20]              // anker op midden van de pijl
    });
}

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
    const osmLayer = L.tileLayer('/tiles/osm/{z}/{x}/{y}.png', {
        attribution: '© OpenStreetMap',
        maxZoom: 19
    });

    const satelliteLayer = L.tileLayer('/tiles/sat/{z}/{x}/{y}.png', {
        attribution: '© Esri',
        maxZoom: 19
    });

    const hybridLayer = L.tileLayer('/tiles/hyb/{z}/{x}/{y}.png', {
        maxZoom: 19
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
 * Update drone positie + heading. Wordt bij elke status_update aangeroepen.
 * Negeer (0,0) coords — dat is "geen fix".
 *
 * @param {number} lat      latitude
 * @param {number} lon      longitude
 * @param {number} alt      hoogte in meters
 * @param {number} heading  graden 0-359, default 0 (noord)
 */
function updateMap(lat, lon, alt, heading) {
    // Skip lege/ongeldige coords
    if ((lat === 0 && lon === 0) || lat === null || lon === null) {
        return;
    }

    // Default heading naar 0 als niet meegegeven (backwards compatibility)
    const hdg = (typeof heading === 'number') ? heading : 0;

    // Eerste echte fix: zoom in, plaats marker, verberg badge
    if (!hasGpsFix) {
        hasGpsFix = true;

        droneMarker = L.marker([lat, lon], { icon: createDroneIcon(hdg) }).addTo(map);
        map.setView([lat, lon], FIX_ZOOM);

        // Verberg "wachten op fix" badge
        const badge = document.getElementById('gps-waiting-badge');
        if (badge) badge.style.display = 'none';

        console.log(`[map] Eerste GPS-fix: ${lat.toFixed(7)}, ${lon.toFixed(7)}, heading ${hdg}°`);
    } else {
        // Positie updaten
        droneMarker.setLatLng([lat, lon]);

        // Heading updaten — we hergebruiken de div binnen het marker-element
        // en zetten alleen de transform, zodat we niet bij elke update een
        // nieuwe DOM-node hoeven te maken (efficienter + voorkomt flikker).
        const el = droneMarker.getElement();
        if (el) {
            const inner = el.querySelector('.drone-marker');
            if (inner) inner.style.transform = `rotate(${hdg}deg)`;
        }
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