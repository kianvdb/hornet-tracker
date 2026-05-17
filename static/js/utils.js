/**
 * UTILS — pure helper functies zonder eigen state
 *
 * Bevat:
 *  - rssiToBars(rssi)         RSSI dBm  -> aantal balkjes (0-4)
 *  - rssiLabel(rssi)          RSSI dBm  -> {text, cls} voor UI label
 *  - updateBars(id, bars)     pas .bars-N class toe op signal-bars element
 *  - setCardStatus(id, lvl)   pas .status-{good,ok,warn,bad} toe op quick card
 *  - barsToStatusLevel(...)   convert bars + connected flag -> status level
 *  - showToast(msg)           toon korte notificatie rechtsonder (2.5s)
 *
 * Alle functies worden op window gezet zodat ze direct als globals
 * werken vanuit andere modules en HTML onclick handlers.
 */

/**
 * Convert RSSI in dBm to visual signal bars (0-4).
 * Drempels gekozen voor 2.4 GHz wifi + 433 MHz SiK telemetrie.
 */
function rssiToBars(rssi) {
    if (rssi >= -50) return 4;
    if (rssi >= -65) return 3;
    if (rssi >= -75) return 2;
    if (rssi >= -90) return 1;
    return 0;
}

/**
 * RSSI -> menselijk label + CSS class voor kleur.
 * Returned object met {text, cls} structure.
 */
function rssiLabel(rssi) {
    if (rssi >= -50) return { text: 'Uitstekend',    cls: 'label-good' };
    if (rssi >= -65) return { text: 'Goed',          cls: 'label-good' };
    if (rssi >= -75) return { text: 'Redelijk',      cls: 'label-ok'   };
    if (rssi >= -90) return { text: 'Zwak',          cls: 'label-warn' };
    return            { text: 'Geen signaal',         cls: 'label-bad'  };
}

/**
 * Update aantal actieve balkjes op een .signal-bars element.
 * Verwijdert vorige bars-N class en zet nieuwe.
 */
function updateBars(elementId, bars) {
    document.getElementById(elementId).className = 'signal-bars bars-' + bars;
}

/**
 * Zet status-kleur class (good/ok/warn/bad) op een quick card.
 * Verwijdert eerst eventuele oude status- class.
 */
function setCardStatus(cardId, level) {
    const card = document.getElementById(cardId);
    card.classList.remove('status-good', 'status-ok', 'status-warn', 'status-bad');
    card.classList.add('status-' + level);
}

/**
 * Vertaal aantal balkjes + connected flag naar status level voor card border.
 *  0 of disconnected => bad
 *  1                 => warn
 *  2                 => ok
 *  3 of 4            => good
 */
function barsToStatusLevel(bars, connected) {
    if (!connected || bars === 0) return 'bad';
    if (bars === 1) return 'warn';
    if (bars === 2) return 'ok';
    return 'good';
}

/**
 * Toon korte notificatie rechtsonder voor 2.5 seconden.
 * Gebruikt voor "gekopieerd", "gelogd", error-meldingen.
 */
function showToast(msg) {
    const toast = document.getElementById('toast');
    toast.textContent = msg;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 2500);
}

// Expose op window voor toegang vanuit andere modules en HTML onclick handlers
window.rssiToBars        = rssiToBars;
window.rssiLabel         = rssiLabel;
window.updateBars        = updateBars;
window.setCardStatus     = setCardStatus;
window.barsToStatusLevel = barsToStatusLevel;
window.showToast         = showToast;
