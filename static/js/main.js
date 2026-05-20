/**
 * MAIN — bootstrap van de dashboard frontend
 *
 * Wordt als laatste script geladen. Verantwoordelijk voor:
 *   1. Socket.io verbinding opzetten
 *   2. window.socket exposen voor andere modules
 *   3. Map initialiseren (op fallback locatie Brussel)
 *   4. Coordinaat-log placeholder renderen
 *   5. Alle module-specifieke socket handlers registreren
 *
 * Volgorde is belangrijk: socket moet bestaan voordat handlers worden
 * geregistreerd, en de DOM moet klaar zijn voordat we elementen aanraken.
 *
 * Alle module-functies zijn al op window gezet door hun respectievelijke
 * modules (utils.js, map.js, coord-log.js, drone-controls.js,
 * signal-display.js, socket-handlers.js, modals.js).
 */

(function bootstrap() {
    // ============================================
    // 1. Socket.io verbinding
    // ============================================
    const socket = io();
    window.socket = socket;   // expose voor modals.js, drone-controls.js, etc.

    // ============================================
    // 2. DOM bootstrap
    // ============================================
    document.addEventListener('DOMContentLoaded', function() {
        console.log('[main] DOM ready, initialiseren...');

        // Map laadt direct op fallback locatie (Brussel),
        // zoomt later in op echte GPS-fix.
        window.initMap();

      window.loadCoordLogFromStorage();   // herstelt log na refresh
        window.updateLogDisplay();

        // Module-specifieke socket handlers registreren.
        // Volgorde maakt niet uit — ze luisteren op verschillende events.
        window.registerCoreSocketHandlers();          // connect / disconnect / status_update
        window.registerCommandResultHandler();        // arm/disarm/mode feedback
        window.registerBaselineStatusHandler();       // baseline reset knop state
        window.registerPopoverOutsideClick();  

        console.log('[main] Klaar.');
    });
})();
