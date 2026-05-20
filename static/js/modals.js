/**
 * MODALS — shutdown / reboot / countdown / success dialoogvensters
 *
 * Bevat:
 *  - showShutdownModal() / showRebootModal()   open bevestigingsdialoog
 *  - hideModals()                              sluit alle modals + reset countdown
 *  - startCountdown(seconds, onComplete)       countdown timer in modal
 *  - doShutdown() / doReboot()                 stuur commando + start countdown
 *
 * Afhankelijkheden:
 *  - window.socket          (Socket.io client, gezet door main.js)
 *
 * Modals worden via .show class in/uit zicht gezet (zie controls.css).
 */

let countdownInterval = null;

function showShutdownModal() {
    document.getElementById('shutdown-modal').classList.add('show');
}

function showRebootModal() {
    document.getElementById('reboot-modal').classList.add('show');
}

/**
 * Sluit alle modals en stop een eventueel lopende countdown.
 * Wordt aangeroepen bij annuleren, bij succes-OK, en bij socket reconnect.
 */
function hideModals() {
    ['shutdown-modal', 'reboot-modal', 'countdown-modal', 'success-modal', 'arm-modal', 'log-modal', 'export-modal']
        .forEach(id => document.getElementById(id).classList.remove('show'));

    if (countdownInterval) {
        clearInterval(countdownInterval);
        countdownInterval = null;
    }
}

/**
 * Start countdown van X seconden in countdown-modal.
 * Roept onComplete callback aan bij 0.
 */
function startCountdown(seconds, onComplete) {
    let remaining = seconds;
    const numEl = document.getElementById('countdown-number');
    const msgEl = document.getElementById('countdown-message');

    document.getElementById('countdown-modal').classList.add('show');

    function tick() {
        numEl.textContent = remaining;
        msgEl.textContent = `Wacht ${remaining} seconden...`;
        if (remaining <= 0) {
            clearInterval(countdownInterval);
            countdownInterval = null;
            onComplete();
        }
        remaining--;
    }

    tick();
    countdownInterval = setInterval(tick, 1000);
}

/**
 * Stuur shutdown-commando naar Pi en toon 10s countdown.
 * Na countdown verschijnt success-modal ("veilig om los te koppelen").
 */
function doShutdown() {
    hideModals();
    document.getElementById('countdown-title').textContent = '⏻ Afsluiten...';
    window.socket.emit('shutdown');
    startCountdown(10, () => {
        document.getElementById('countdown-modal').classList.remove('show');
        document.getElementById('success-modal').classList.add('show');
    });
}

/**
 * Stuur reboot-commando naar Pi.
 * Geen countdown van X naar 0; reboot duurt 1-2 minuten en wordt
 * uiteindelijk gesignaleerd door socket reconnect (zie socket-handlers).
 */
function doReboot() {
    hideModals();
    document.getElementById('countdown-title').textContent = '🔄 Herstarten...';
    document.getElementById('countdown-modal').classList.add('show');
    document.getElementById('countdown-number').textContent = '...';
    document.getElementById('countdown-message').textContent =
        'Dashboard is over 1-2 minuten weer beschikbaar.';
    window.socket.emit('reboot');
}

// Expose op window voor HTML onclick handlers
window.showShutdownModal = showShutdownModal;
window.showRebootModal   = showRebootModal;
window.hideModals        = hideModals;
window.doShutdown        = doShutdown;
window.doReboot          = doReboot;
