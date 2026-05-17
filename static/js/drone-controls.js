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
 *  - DOM: #command-feedback   tekstveld voor status van laatste commando
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
 * Update tekst + kleur van het feedback element onder de knoppen.
 * Niet exposed op window — alleen intern gebruik.
 */
function setFeedback(text, color) {
    const fb = document.getElementById('command-feedback');
    fb.textContent = text;
    fb.style.color = color;
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
        // Server reageerde — cancel pending timeout
        if (commandTimeoutHandle) {
            clearTimeout(commandTimeoutHandle);
            commandTimeoutHandle = null;
        }

        if (data.success) {
            setFeedback('✅ ' + data.message, '#4ade80');
        } else {
            setFeedback('❌ ' + data.message, '#f87171');
        }
        setTimeout(() => {
            setFeedback("Klaar voor commando's", '#aaa');
        }, 4000);
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
        // Reset na 4s zoals normale command_result feedback
        setTimeout(() => setFeedback("Klaar voor commando's", '#aaa'), 4000);
    }, COMMAND_TIMEOUT_MS);
}
// Expose op window
window.armDrone                     = armDrone;
window.confirmArm                   = confirmArm;
window.disarmDrone                  = disarmDrone;
window.setMode                      = setMode;
window.updateModeButtons            = updateModeButtons;
window.registerCommandResultHandler = registerCommandResultHandler;
