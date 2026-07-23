/**
 * DRONE-CONTROLS — arm/disarm/mode commando's + feedback
 *
 * Bevat:
 *  - armDrone()              vraag bevestiging, stuur arm_drone event
 *  - disarmDrone()            stuur disarm_drone event (geen bevestiging - safety)
 *  - setMode(modeId)          stuur set_mode event met mode ID
 *  - updateModeButtons(mode)  highlight de actieve mode button
 *
 * Bevat ook de socket.on('command_result') handler omdat die uitsluitend
 * feedback geeft op commando's vanuit deze module — geen reden om hem
 * elders te plaatsen.
 *
 * Afhankelijkheden:
 *  - window.socket            Socket.io client (gezet door main bootstrap)
 *  - DOM: #mission-status     missie-statusbalk; command_result-meldingen
 *                             landen hier sinds #command-feedback verdween
 *  - DOM: .mode-btn           knoppen die we highlighten op huidige mode
 */
/**
 * Hoe lang we wachten op command_result van server voor we
 * "geen reactie" tonen. 5 seconden is ruim voldoende — een
 * MAVLink commando round-trip is normaal <1 seconde.
 */
const COMMAND_TIMEOUT_MS = 5000;

/** Houdt het huidige timeout-handle bij zodat we het kunnen cancelen */
let commandTimeoutHandle = null;

// ============================================
// COMMANDO'S
// ============================================

/**
 * Stuur ARM commando na expliciete bevestiging.
 * Safety: confirm() blokkert event-loop tot operator OK/Annuleer kiest.
 */
/**
 * Toon ARM bevestigingsmodal. Operator moet expliciet bevestigen
 * via confirmArm() voor het commando verstuurd wordt.
 */
function armDrone() {
    document.getElementById('arm-modal').classList.add('show');
}

/**
 * Aangeroepen vanuit de modal "ARMEN" knop. Sluit modal en verstuur
 * het arm_drone commando.
 */
function confirmArm() {
    window.hideModals();
    setFeedback('⏳ ARM commando versturen...', '#f5a623');
    window.socket.emit('arm_drone');
    armCommandTimeout();
}
/**
 * Stuur DISARM commando. Geen bevestiging — disarm is altijd veilig en
 * moet snel kunnen (bijv. tijdens noodlanding).
 */
function disarmDrone() {
    setFeedback('⏳ DISARM commando versturen...', '#f5a623');
    window.socket.emit('disarm_drone');
    armCommandTimeout();
}

/**
 * Stuur set_mode commando met ArduCopter mode ID.
 * Mode mapping zie COPTER_MODES in app.py mavlink_loop().
 */
function setMode(modeId) {
    const modeNames = { 0:'STABILIZE', 2:'ALT_HOLD', 4:'GUIDED',
                        5:'LOITER', 6:'RTL', 9:'LAND' };
    const name = modeNames[modeId] || 'MODE_' + modeId;
    setFeedback('⏳ Mode → ' + name + '...', '#f5a623');
    window.socket.emit('set_mode', { mode: modeId });
    armCommandTimeout();
}

// ============================================
// UI HELPERS
// ============================================

/**
 * Toon een servermelding in de missie-statusbalk.
 *
 * Schreef eerder naar #command-feedback, dat met de vereenvoudiging van
 * de besturing-card verdwenen is. De statusbalk naast de armed-badge is
 * nu de enige plek waar meldingen verschijnen — client-side (via
 * setMissionStatus in mission-controls.js) en server-side (hier) landen
 * dus op hetzelfde element.
 *
 * Kleur volgt het succes-veld van command_result: groen bij geslaagd,
 * rood bij geweigerd. Zonder expliciet succes-signaal blijft het neutraal.
 *
 * @param {string} text      de melding
 * @param {boolean} [success] true = groen, false = rood, undefined = grijs
 */
function setFeedback(text, success) {
    const el = document.getElementById('mission-status');
    if (!el) return;

    el.textContent = text;
    if (success === true)       el.style.color = '#4ade80';
    else if (success === false) el.style.color = '#f87171';
    else                        el.style.color = '#aaa';
}

/**
 * Highlight de mode-button die met de huidige flight_mode overeenkomt.
 * Wordt bij elke status_update aangeroepen vanuit socket-handlers.
 */
function updateModeButtons(currentMode) {
    const btns = document.querySelectorAll('.mode-btn');
    btns.forEach(btn => {
        if (btn.textContent === currentMode) {
            btn.style.borderColor = '#4ade80';
            btn.style.color = '#4ade80';
        } else {
            btn.style.borderColor = '#0f3460';
            btn.style.color = '#eee';
        }
    });
}

// ============================================
// SOCKET EVENT — command_result
// ============================================

/**
 * Server stuurt command_result terug na elke arm/disarm/set_mode poging.
 * data = { success: bool, message: string }
 *
 * We registreren de handler hier i.p.v. in socket-handlers.js omdat de
 * volledige UI-logica (feedback element kleur + reset na 4s) bij deze
 * module hoort. Wordt aangeroepen vanuit main bootstrap.
 */
function registerCommandResultHandler() {
    window.socket.on('command_result', function(data) {
        // Server reageerde — cancel pending timeout (alleen relevant voor de
        // console-only arm/disarm/set_mode-flows die de watchdog nog zetten)
        if (commandTimeoutHandle) {
            clearTimeout(commandTimeoutHandle);
            commandTimeoutHandle = null;
        }

        // Servermelding in de missie-statusbalk, gekleurd op succes. Geen
        // auto-reset-timer: die zou een volgende mission_update-stap kunnen
        // overschrijven — tijdens een vlucht wil je de actuele toestand zien.
        setFeedback(data.message, data.success);
    });
}
/**
 * Start een timeout die "geen reactie" toont als de server geen
 * command_result terugstuurt binnen COMMAND_TIMEOUT_MS. Wordt aangeroepen
 * direct na elk emit van arm_drone, disarm_drone, set_mode.
 *
 * De timeout wordt automatisch gecanceld in command_result handler.
 */
function armCommandTimeout() {
    if (commandTimeoutHandle) clearTimeout(commandTimeoutHandle);
    commandTimeoutHandle = setTimeout(() => {
        setFeedback('❌ Geen reactie van Pi/Pixhawk', '#f87171');
        commandTimeoutHandle = null;
        // Reset uitgeschakeld: #mission-status is gedeeld met de missievoortgang
        // en een timer die daaroverheen schrijft zou een missiestap kunnen
        // maskeren. Verdwijnt met de manual-arm-cleanup.
        // setTimeout(() => setFeedback("Klaar voor commando's", '#aaa'), 4000);
    }, COMMAND_TIMEOUT_MS);
}
// Expose op window
window.armDrone                     = armDrone;
window.confirmArm                   = confirmArm;
window.disarmDrone                  = disarmDrone;
window.setMode                      = setMode;
window.updateModeButtons            = updateModeButtons;
window.registerCommandResultHandler = registerCommandResultHandler;
