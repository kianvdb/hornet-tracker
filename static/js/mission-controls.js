/**
 * MISSION-CONTROLS — zoekvlucht starten + noodknoppen
 *
 * Bevat:
 *  - startMission()        start de autonome ZOEKVLUCHT (search.py)
 *  - missionRTL()          EINDE MISSIE: terug naar opstijgpunt en landen
 *  - missionStopHang()     LOITER (niet aan een knop gekoppeld, zie hieronder)
 *  - missionLand()         LAND (niet aan een knop gekoppeld, zie hieronder)
 *  - registerMissionHandlers()      luistert naar mission_update van server
 *
 * VEILIGHEIDSMODEL (ter herinnering in de code):
 *  - De zender is primair. EINDE MISSIE (RTL) is een backup van de
 *    SwC-switch. LAND en LOITER zitten alleen op de zender.
 *  - Disarmen gebeurt nooit vanuit de lucht. Elke landing (missie/RTL/LAND)
 *    leidt tot auto-disarm door ArduCopter zelf.
 *
 * Afhankelijkheden:
 *  - window.socket                 Socket.io client (gezet door main bootstrap)
 *  - DOM: #mission-status          voortgangstekst van de missie
 *  - reuse: setFeedback()          uit drone-controls.js (feedback-element)
 */

// Minimale GPS-eisen — moeten matchen met MIN_SATELLITES in search.py
const MISSION_MIN_SATS = 8;

// Zoekhoogte-grenzen — moeten matchen met MIN/MAX_TAKEOFF_ALT_M in mission.py
// (search.py clampt via mission._clamp_altitude, dus dezelfde grens).
// De server clampt opnieuw; deze waarden zijn UX, geen veiligheid.
// De bovengrens komt uit de geofence (FENCE_ALT_MAX = 10 m) minus marge
// voor overshoot en wind — zie de toelichting in mission.py.
const MISSION_MIN_ALT_M     = 1.5;
const MISSION_MAX_ALT_M     = 5.0;
const MISSION_DEFAULT_ALT_M = 2.5;


// ============================================
// MISSIE-COMMANDO'S
// ============================================

/**
 * Lees de ingestelde zoekhoogte uit het invoerveld en clamp hem op het
 * toegestane bereik.
 *
 * Waarom clampen in de browser terwijl de server het ook doet: de
 * min/max-attributen op een number-input zijn een hint voor de spinner,
 * geen garantie — getypte waarden buiten het bereik komen er gewoon door.
 * Deze clamp voorkomt dat we een onmogelijke hoogte versturen. De echte
 * grens ligt in mission.py.
 *
 * @returns {number} hoogte in meters, altijd binnen [MIN, MAX]
 */
function readTakeoffAltitude() {
    const el = document.getElementById('mission-alt');
    if (!el) return MISSION_DEFAULT_ALT_M;

    const val = parseFloat(el.value);
    if (isNaN(val)) return MISSION_DEFAULT_ALT_M;

    return Math.max(MISSION_MIN_ALT_M, Math.min(MISSION_MAX_ALT_M, val));
}

/**
 * Start de autonome zoekvlucht. We tonen meteen feedback; de echte voortgang
 * komt via meting_update events van de server (zie pattern-controls.js) —
 * hetzelfde kanaal als de metingen, zodat alles op één statusregel landt.
 *
 * De knop heet nog START MISSIE en start sinds de koppeling aan search.py
 * de zoekvlucht in plaats van de demo-missie. Die demo-missie bestaat nog
 * als terugval en start je vanuit de browserconsole:
 *     socket.emit('start_demo_mission', { altitude: 2.5 })
 *
 * Veiligheid: we checken eerst de pre-flight status client-side zodat de
 * operator niet per ongeluk start zonder GPS-fix. De server checkt het nog
 * een keer (search.py pre-flight, inclusief een beacon-check), dus dit is
 * alleen UX.
 *
 * De zoekhoogte komt uit het #mission-alt veld en wordt meegestuurd in de
 * payload; search.py gebruikt hem als starthoogte voor het peilen en clampt
 * hem opnieuw.
 */
function startMission() {
    // Client-side pre-flight waarschuwing (server checkt ook)
    const lastStatus = window.lastKnownStatus || {};
    const sats = lastStatus.gps_satellites || 0;
    const hasFix = lastStatus.gps_fix || false;

    if (!hasFix || sats < MISSION_MIN_SATS) {
        const doorgaan = confirm(
            `⚠️ GPS nog niet ideaal (fix: ${hasFix ? 'ja' : 'nee'}, ` +
            `satellieten: ${sats}/${MISSION_MIN_SATS}).\n\n` +
            `De missie kan geweigerd worden. Toch proberen te starten?`
        );
        if (!doorgaan) return;
    }

    const hoogte = readTakeoffAltitude();

    setMissionStatus(`🚀 Zoekvlucht starten (${hoogte} m)...`, '#f5a623');
    window.socket.emit('start_mission', { altitude: hoogte });
}

/**
 * STOP & HANG — zet de drone in LOITER.
 *
 * Niet meer aan een knop gekoppeld sinds de besturing-card tot twee
 * knoppen is teruggebracht: LOITER zit op de SwC-switch van de zender.
 * De functie en de server-handler blijven bestaan zodat de actie via
 * de console beschikbaar is tijdens tests.
 */
function missionStopHang() {
    setMissionStatus('✋ STOP & HANG (LOITER)...', '#f5a623');
    window.socket.emit('mission_stop_hang');
}

/**
 * RTL — terug naar opstijgpunt en landen. Vraagt geen bevestiging omdat
 * het een veilige actie is (drone komt gecontroleerd thuis).
 */
function missionRTL() {
    setMissionStatus('🏠 RTL — terugkeren...', '#f5a623');
    window.socket.emit('mission_rtl');
}

/**
 * LAND NU — direct dalen en landen op huidige plek.
 *
 * Niet meer aan een knop gekoppeld sinds de besturing-card tot twee
 * knoppen is teruggebracht: LAND zit op de SwC-switch van de zender.
 * De functie en de server-handler blijven bestaan zodat de actie via
 * de console beschikbaar is tijdens tests.
 */
function missionLand() {
    setMissionStatus('🛬 LAND NU — dalen...', '#f5a623');
    window.socket.emit('mission_land');
}


// ============================================
// SERVER-EVENTS
// ============================================

/**
 * Registreer de mission_update handler. Dat kanaal is nu alleen nog van de
 * demo-missie (mission.py); de zoekvlucht meldt via meting_update. Beide
 * schrijven naar #mission-status.
 */
function registerMissionHandlers() {
    window.socket.on('mission_update', function(data) {
        let kleur = '#5e8bff';   // blauw = bezig
        if (data.step === 'klaar')  kleur = '#4ade80';   // groen = klaar
        if (data.step === 'fout')   kleur = '#f87171';   // rood = fout
        if (data.step === 'gestopt') kleur = '#f5a623';  // oranje = piloot nam over

        setMissionStatus(data.message || data.step, kleur);

        // Bij einde (klaar/fout/gestopt): reset na een tijdje naar idle
        if (!data.active) {
            setTimeout(() => {
                setMissionStatus('Klaar om te starten', '#aaa');
            }, 6000);
        }
    });
}


// ============================================
// UI HELPER
// ============================================

/**
 * Update de missie-voortgangstekst met kleur.
 *
 * Let op: #mission-status wordt ook beschreven door setFeedback() in
 * drone-controls.js, dat servermeldingen (command_result) toont. Beide
 * schrijven bewust naar hetzelfde element — de laatste melding wint,
 * wat de actuele toestand is.
 */
function setMissionStatus(text, color) {
    const el = document.getElementById('mission-status');
    if (!el) return;
    el.textContent = text;
    el.style.color = color;
}


// Expose op window
window.startMission           = startMission;
window.missionStopHang        = missionStopHang;
window.missionRTL             = missionRTL;
window.missionLand            = missionLand;
window.readTakeoffAltitude    = readTakeoffAltitude;
window.registerMissionHandlers = registerMissionHandlers;