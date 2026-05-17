/**
 * SIGNAL-DISPLAY — LoRa signal card update logica + baseline reset
 *
 * Bevat:
 *  - updateSignalCard(data)         render power/baseline/delta/bar/detectie
 *  - resetBaseline()                stuur reset_baseline event naar server
 *  - registerBaselineStatusHandler()  handler voor server feedback tijdens meten
 *
 * Afhankelijkheden:
 *  - window.socket                  Socket.io client
 *  - DOM elementen in de signal-card (#signal-power, #signal-baseline,
 *    #signal-delta, #signal-bar, #signal-detected, #reset-baseline-btn)
 *
 * De signal-bar wordt geschaald op basis van delta (dB boven baseline):
 *   delta = -10 dB  =>  0% breedte
 *   delta = +23 dB  => 100% breedte
 *   formule:           (delta + 10) * 3, geclamped op [0, 100]
 */

/**
 * Vraag de Pi om opnieuw een baseline-meting uit te voeren.
 * Server stuurt baseline_status events tijdens en na de meting.
 */
function resetBaseline() {
    const btn = document.getElementById('reset-baseline-btn');
    btn.disabled = true;
    btn.textContent = 'Meten...';
    window.socket.emit('reset_baseline');
}

/**
 * Update alle elementen in de LoRa signal card op basis van status_update.
 * Wordt vanuit socket-handlers aangeroepen.
 *
 * @param {Object} data  status object van server (zie app.py status dict)
 */
function updateSignalCard(data) {
    // Numerieke waardes
    document.getElementById('signal-power').textContent    = data.signal_power.toFixed(1) + ' dB';
    document.getElementById('signal-baseline').textContent = data.baseline.toFixed(1) + ' dB';

    // Delta met expliciet + teken voor positieve waardes (leesbaarheid)
    const deltaSign = data.signal_delta >= 0 ? '+' : '';
    document.getElementById('signal-delta').textContent = deltaSign + data.signal_delta.toFixed(1) + ' dB';

    // Signal bar breedte schaalt delta -10..+23 dB naar 0..100%
    const barWidth = Math.min(100, Math.max(0, (data.signal_delta + 10) * 3));
    document.getElementById('signal-bar').style.width = barWidth + '%';

    // Detectie label + kleur van het grote delta-getal
    const detectedEl = document.getElementById('signal-detected');
    const deltaEl    = document.getElementById('signal-delta');
    if (data.signal_detected) {
        detectedEl.textContent = '>>> SIGNAAL <<<';
        detectedEl.className   = 'status-value signal-detected';
        deltaEl.style.color    = '#4ade80';
    } else {
        detectedEl.textContent = 'Geen signaal';
        detectedEl.className   = 'status-value';
        deltaEl.style.color    = '#aaa';
    }
}

/**
 * Server stuurt baseline_status events:
 *   { measuring: true }                       tijdens meting
 *   { measuring: false, baseline: <dB> }      na voltooiing
 *
 * We disablen de reset-knop tijdens meten zodat operator niet 2x klikt.
 */
function registerBaselineStatusHandler() {
    window.socket.on('baseline_status', function(data) {
        const btn = document.getElementById('reset-baseline-btn');
        btn.disabled = data.measuring;
        btn.textContent = data.measuring ? 'Meten...' : 'Reset';
    });
}

// Expose op window
window.resetBaseline                  = resetBaseline;
window.updateSignalCard               = updateSignalCard;
window.registerBaselineStatusHandler  = registerBaselineStatusHandler;
