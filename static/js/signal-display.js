/**
 * SIGNAL-DISPLAY — LoRa signal card update logica
 *
 * Render-strategie:
 *   - Tier 1: RSSI (groot getal) + detectie badge + absolute bar
 *   - Tier 2: SNR + laatste packet age (link-kwaliteit)
 *   - Tier 3: packet count + tracker ID (administratief)
 *
 * RSSI-bar mapt -120 dBm (links, leeg) tot -40 dBm (rechts, vol):
 *   bar_pct = (rssi + 120) / 80 * 100
 *   clampt op [0, 100]
 *
 * Detectie-criterium:
 *   - signal_detected = true wanneer packet binnen 3s ontvangen
 *   - bepaald door de backend, frontend toont alleen
 *
 * Update-bron:
 *   - status_update events bevatten signal_power, signal_detected,
 *     lora_packet_count, lora_last_tracker_id, lora_last_seen_sec, lora_snr
 *   - signal_loop in app.py emits ~10 Hz
 */


/**
 * Map RSSI (dBm) naar percentage voor de bar.
 * -120 = 0%, -40 = 100%, lineair daartussen.
 */
function rssiToPercent(rssi) {
    const pct = ((rssi + 120) / 80) * 100;
    return Math.max(0, Math.min(100, pct));
}


/**
 * Format laatste-packet-age in een leesbare string.
 * < 1s    -> "0.X s geleden"
 * < 60s   -> "XX.X s geleden"
 * >= 60s  -> "XX min geleden" (operator wil weten wanneer link weg ging)
 * -1      -> "Nog geen packet" (sinds service start)
 */
function formatPacketAge(seconds) {
    if (seconds < 0) return 'Nog geen packet';
    if (seconds < 60) return seconds.toFixed(1) + ' s geleden';
    const mins = Math.floor(seconds / 60);
    return `${mins} min geleden`;
}


/**
 * Update LoRa signal card op basis van status_update.
 * Wordt aangeroepen vanuit socket-handlers bij elke status update.
 *
 * @param {Object} data  status object van server
 */
function updateSignalCard(data) {
    // TIER 1: RSSI groot + detectie badge
    const rssiEl = document.getElementById('lora-rssi');
    const badgeEl = document.getElementById('lora-detect-badge');
    const barEl = document.getElementById('lora-bar-fill');

    if (rssiEl) {
        rssiEl.textContent = data.signal_power.toFixed(0) + ' dBm';
    }

    if (badgeEl) {
        if (data.signal_detected) {
            badgeEl.textContent = '● Signaal';
            badgeEl.classList.add('active');
        } else {
            badgeEl.textContent = 'Geen signaal';
            badgeEl.classList.remove('active');
        }
    }

    if (barEl) {
        barEl.style.width = rssiToPercent(data.signal_power) + '%';
    }

    // TIER 2: SNR + packet age
    const snrEl = document.getElementById('lora-snr');
    const ageEl = document.getElementById('lora-last-seen');

    if (snrEl) {
        const snr = data.lora_snr || 0;
        const sign = snr >= 0 ? '+' : '';
        snrEl.textContent = sign + snr.toFixed(1) + ' dB';
    }

    if (ageEl) {
        const age = data.lora_last_seen_sec;
        ageEl.textContent = formatPacketAge(typeof age === 'number' ? age : -1);
    }

    // TIER 3: packet count + tracker ID
    const countEl = document.getElementById('lora-packet-count');
    const trackerEl = document.getElementById('lora-tracker-id');

    if (countEl) {
        countEl.textContent = data.lora_packet_count || 0;
    }

    if (trackerEl) {
        const id = data.lora_last_tracker_id;
        trackerEl.textContent = (id && id > 0) ? `ID ${id}` : 'ID --';
    }
}


/**
 * Stub voor backwards-compatibility met socket-handlers.js dat
 * deze functie verwacht. LoRa heeft geen baseline-meting meer, dus
 * deze handler doet niets — server stuurt baseline_status events
 * niet meer in LoRa-modus.
 *
 * Wordt nog wel geregistreerd voor de RTL-SDR fallback-pad.
 */
function registerBaselineStatusHandler() {
    if (!window.socket) return;
    window.socket.on('baseline_status', function(data) {
        // RTL-SDR-pad: backend stuurt nog steeds baseline events.
        // LoRa-modus stuurt geen baseline events, deze handler doet niets.
        // Stub bewaren zodat socket-handlers.js niet faalt op missing function.
    });
}


/**
 * Stub voor backwards-compatibility. Reset-baseline knop is verwijderd
 * uit de UI in LoRa-modus. Behouden zodat geen ReferenceError optreedt
 * als ergens nog een legacy onclick handler verwijst.
 */
function resetBaseline() {
    if (!window.socket) return;
    window.socket.emit('reset_baseline');
}


// Expose op window
window.updateSignalCard              = updateSignalCard;
window.registerBaselineStatusHandler = registerBaselineStatusHandler;
window.resetBaseline                 = resetBaseline;