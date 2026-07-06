/**
 * MISSION-CONTROLS — demo-missie besturing + noodknoppen + pre-flight status
 *
 * Bevat:
 *  - startMission()        start de autonome demo-sequentie
 *  - missionStopHang()     LOITER: blijf hangen op huidige positie
 *  - missionRTL()          terug naar opstijgpunt en landen
 *  - missionLand()         direct dalen en landen op huidige plek
 *  - updatePreflightStatus(status)  vult GPS/sats/RTL-blok
 *  - registerMissionHandlers()      luistert naar mission_update van server
 *
 * VEILIGHEIDSMODEL (ter herinnering in de code):
 *  - De zender is primair. Deze knoppen zijn backup, behalve LAND NU
 *    (die zit niet op de switch).
 *  - Disarmen gebeurt nooit vanuit de lucht. Elke landing (missie/RTL/LAND)
 *    leidt tot auto-disarm door ArduCopter zelf.
 *
 * Afhankelijkheden:
 *  - window.socket                 Socket.io client (gezet door main bootstrap)
 *  - DOM: #mission-status          voortgangstekst van de missie
 *  - DOM: #pf-gps / #pf-sats / #pf-rtl-alt   pre-flight statusvelden
 *  - reuse: setFeedback()          uit drone-controls.js (feedback-element)
 */

// Minimale GPS-eisen — moeten matchen met MIN_SATELLITES in mission.py
const MISSION_MIN_SATS = 8;

// De RTL-hoogte lezen we niet live uit params (dat vereist een aparte
// MAVLink-query); we tonen de waarde die we bij de Pixhawk hebben gezet.
// Pas dit aan als je RTL_ALT wijzigt. In cm gedeeld door 100 = meters.
const RTL_ALT_METERS = 4.0;   // matcht RTL_ALT=400 die we hebben gezet


// ============================================
// MISSIE-COMMANDO'S
// ============================================

/**
 * Start de autonome demo-missie. We tonen meteen feedback; de echte
 * voortgang komt via mission_update events van de server.
 *
 * Veiligheid: we checken eerst de pre-flight status client-side zodat de
 * operator niet per ongeluk start zonder GPS-fix. De server checkt het
 * nog een keer (mission.py pre-flight), dus dit is alleen UX.
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

    setMissionStatus('🚀 Missie starten...', '#f5a623');
    window.socket.emit('start_mission');
}

/**
 * STOP & HANG — zet de drone in LOITER. Hij blijft hangen op zijn huidige
 * positie en hoogte tot de operator een volgende stap kiest.
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
 * LAND NU — direct dalen en landen op huidige plek. De enige nood-actie
 * die niet op de zender-switch zit.
 */
function missionLand() {
    setMissionStatus('🛬 LAND NU — dalen...', '#f5a623');
    window.socket.emit('mission_land');
}


// ============================================
// PRE-FLIGHT STATUS
// ============================================

/**
 * Vul het pre-flight statusblok met live GPS-data. Wordt bij elke
 * status_update aangeroepen vanuit socket-handlers. Kleurt groen als
 * de drone klaar is om te starten, oranje/rood als niet.
 */
function updatePreflightStatus(status) {
    const gpsEl = document.getElementById('pf-gps');
    const satsEl = document.getElementById('pf-sats');
    const rtlEl = document.getElementById('pf-rtl-alt');
    if (!gpsEl || !satsEl || !rtlEl) return;

    const hasFix = status.gps_fix || false;
    const sats = status.gps_satellites || 0;

    // GPS-fix
    gpsEl.textContent = hasFix ? '✅ 3D-fix' : '❌ geen fix';
    gpsEl.style.color = hasFix ? '#4ade80' : '#f87171';

    // Satellieten — groen bij genoeg, oranje bij te weinig
    satsEl.textContent = sats;
    satsEl.style.color = (sats >= MISSION_MIN_SATS) ? '#4ade80' : '#f5a623';

    // RTL-hoogte (statisch, de waarde die we hebben gezet)
    rtlEl.textContent = RTL_ALT_METERS.toFixed(1) + ' m';
}


// ============================================
// SERVER-EVENTS
// ============================================

/**
 * Registreer de mission_update handler. De server (mission.py via emit_fn)
 * stuurt bij elke stap een update met {step, message, active, aborted_by_pilot}.
 * Wordt aangeroepen vanuit main bootstrap.
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

/** Update de missie-voortgangstekst met kleur. */
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
window.updatePreflightStatus  = updatePreflightStatus;
window.registerMissionHandlers = registerMissionHandlers;