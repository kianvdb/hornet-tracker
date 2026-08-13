/**
 * SIGNAL-DISPLAY — hoornaarsignaal card update logica
 *
 * Render-strategie:
 *   - Tier 1: RSSI (groot getal) + detectie badge + absolute bar
 *   - Tier 2: signaalgetrouwheid (SNR) met eigen bar + laatst ontvangen
 *
 * RSSI-bar mapt RSSI_BAR_MIN..RSSI_BAR_MAX (zie hieronder).
 * SNR-bar mapt SNR_BAR_MIN..SNR_BAR_MAX.
 *
 * Naast de absolute waarde tonen we een TREND (sterker/gelijk/zwakker).
 * Dat is bewust: het zoekalgoritme beslist op de verandering van de RSSI,
 * niet op de waarde ervan. Zie berekenTrend().
 *
 * Detectie-criterium:
 *   - signal_detected = true wanneer packet binnen 3s ontvangen
 *   - bepaald door de backend, frontend toont alleen
 *
 * Update-bron:
 *   - status_update events bevatten signal_power, signal_detected,
 *     lora_last_seen_sec, lora_snr
 *   - signal_loop in app.py emits ~10 Hz
 */


/**
 * Balkgrenzen voor de RSSI, gekozen op het WERKELIJK gemeten bereik.
 *
 * Over alle 152 meetpunten uit de zes meetvluchten loopt de RSSI van
 * -108,5 tot -86,0 dBm. Op de oude theoretische schaal (-120..-40 dBm)
 * bewoog de balk over dat hele bereik maar 28% van zijn breedte, en de
 * ~4 dB waar het algoritme een beslissing op neemt was ~5% breed — voor
 * het oog stond de balk stil.
 *
 * Waarden buiten het bereik clampen; een vollere balk dan we ooit gemeten
 * hebben leest terecht als "sterker dan alles wat we kennen".
 */
const RSSI_BAR_MIN = -110;
const RSSI_BAR_MAX = -85;

/** Map RSSI (dBm) naar percentage voor de bar. */
function rssiToPercent(rssi) {
    const pct = ((rssi - RSSI_BAR_MIN) / (RSSI_BAR_MAX - RSSI_BAR_MIN)) * 100;
    return Math.max(0, Math.min(100, pct));
}

/**
 * Balkgrenzen voor de SNR, eveneens op het gemeten bereik (-3,6..+8,8 dB).
 *
 * De ondergrens blijft net onder de SF7-demodulatiegrens (~-7,5 dB) zodat
 * een nog-net-ontvangen packet een zichtbaar gevulde balk geeft — dat was
 * de reden voor de oude -10 dB en die reden geldt nog steeds. -6 dB houdt
 * dat effect en levert wel bruikbare spreiding op: de slechtst gemeten
 * hoek komt op ~27%, de beste op ~99%.
 *
 * Dat verschil IS de richtingaanwijzing: bij het rondkijken varieert de
 * SNR 7-10 dB over de hoek.
 */
const SNR_BAR_MIN = -6;
const SNR_BAR_MAX = 9;

/** Map SNR (dB) naar percentage voor de signaalgetrouwheid-balk. */
function snrToPercent(snr) {
    const pct = ((snr - SNR_BAR_MIN) / (SNR_BAR_MAX - SNR_BAR_MIN)) * 100;
    return Math.max(0, Math.min(100, pct));
}


// ============================================
// TREND — wordt het signaal sterker of zwakker?
// ============================================
//
// Waarom dit er staat: een absolute RSSI zegt uit zichzelf weinig. Uit de
// rotatiemetingen levert de KIJKRICHTING 9-11 dB verschil op, terwijl het
// hele afstandsbereik van 15 naar 6 m maar ~7 dB geeft. "-94 dBm" lezen
// als "zo ver weg" is dus structureel misleidend — dezelfde valkuil waar
// het zoekalgoritme zelf op vastliep tot het aankomstcriterium van een
// absolute drempel naar een heuvelklim ging.
//
// Waar het algoritme wél op beslist is de VERANDERING terwijl de drone
// beweegt: stijgt de RSSI over een stap, dan gaat het de goede kant op.
// Deze indicator toont precies dat, met dezelfde drempel.

/** Spiegelt MIN_STIJGING_DB in search.py — verandert die, verander deze mee. */
const TREND_DREMPEL_DB = 1.5;

/** Vensterlengte. Een 5 m-stap duurt ~5 s; korter dan dit is ruis. */
const TREND_VENSTER_MS = 4000;

/** Onder dit aantal monsters is een mediaan betekenisloos. */
const TREND_MIN_MONSTERS = 6;

/** Ringbuffer van {t, rssi} over het laatste venster. */
let trendGeschiedenis = [];


/** Mediaan van een getallenreeks (leeg -> null). */
function mediaan(waarden) {
    if (!waarden.length) return null;
    const s = [...waarden].sort((a, b) => a - b);
    const m = Math.floor(s.length / 2);
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}


/**
 * Bepaal de trend over het laatste venster.
 *
 * We vergelijken de mediaan van de nieuwste derde met die van de oudste
 * derde. Mediaan en niet gemiddelde, om dezelfde reden als in pattern.py:
 * er zijn losse uitschieters van meerdere dB gezien en die mogen de
 * conclusie niet meeslepen.
 *
 * De server emit ~10 Hz terwijl de beacon 1-2 Hz zendt, dus dezelfde
 * meetwaarde komt meerdere keren langs. Voor een mediaan is dat onschadelijk.
 *
 * @returns {'stijgt'|'daalt'|'gelijk'|null}  null = nog te weinig data
 */
function berekenTrend(rssi, nu) {
    trendGeschiedenis.push({ t: nu, rssi: rssi });
    trendGeschiedenis = trendGeschiedenis.filter(p => nu - p.t <= TREND_VENSTER_MS);

    if (trendGeschiedenis.length < TREND_MIN_MONSTERS) return null;

    // Pas oordelen als het venster ook echt vol is; anders vergelijk je
    // twee momenten die een halve seconde uit elkaar liggen.
    const spanne = nu - trendGeschiedenis[0].t;
    if (spanne < TREND_VENSTER_MS * 0.6) return null;

    const derde = Math.max(2, Math.floor(trendGeschiedenis.length / 3));
    const oud = mediaan(trendGeschiedenis.slice(0, derde).map(p => p.rssi));
    const nieuw = mediaan(trendGeschiedenis.slice(-derde).map(p => p.rssi));

    // De twee medianen zijn zwaartepunten van het eerste en laatste derde,
    // en die liggen maar 2/3 venster uit elkaar — niet een heel venster.
    // Zonder deze correctie ligt de drempel feitelijk op 1,5 / (2/3) =
    // 2,25 dB en meldt de indicator "gelijk" bij een stijging waar het
    // algoritme al op zou handelen. Terugschalen naar een heel venster
    // maakt TREND_DREMPEL_DB weer de drempel die er staat.
    const verschil = (nieuw - oud) / (2 / 3);

    if (verschil >= TREND_DREMPEL_DB) return 'stijgt';
    if (verschil <= -TREND_DREMPEL_DB) return 'daalt';
    return 'gelijk';
}


/** Gooi de trendgeschiedenis weg (signaal verloren). */
function resetTrend() {
    trendGeschiedenis = [];
}


/**
 * Zet de trend in beeld. null = niets tonen (te weinig data of geen signaal);
 * de plek blijft dan leeg in plaats van een oude conclusie te laten hangen.
 *
 * Bewoording: "sterker/zwakker" en niet "dichterbij/verder". Het signaal kan
 * ook toenemen doordat de drone beter gericht staat, en de operator een
 * afstand laten aflezen die er niet in zit is precies de verwarring die we
 * hier oplossen.
 */
function toonTrend(el, trend) {
    if (!el) return;

    if (trend === 'stijgt') {
        el.textContent = '\u2191 sterker';
        el.className = 'lora-trend stijgt';
    } else if (trend === 'daalt') {
        el.textContent = '\u2193 zwakker';
        el.className = 'lora-trend daalt';
    } else if (trend === 'gelijk') {
        el.textContent = '\u2192 gelijk';
        el.className = 'lora-trend gelijk';
    } else {
        el.textContent = '';
        el.className = 'lora-trend';
    }
}

/**
 * Format laatste-packet-age in een leesbare string.
 * < 1s    -> "0.X s geleden"
 * < 60s   -> "XX.X s geleden"
 * >= 60s  -> "XX min geleden" (operator wil weten wanneer link weg ging)
 * -1      -> "Nog niets ontvangen" (sinds service start)
 */
function formatPacketAge(seconds) {
    if (seconds < 0) return 'Nog niets ontvangen';
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

    // Trend naast het getal. Alleen bijhouden zolang er echt packets
    // binnenkomen: valt de beacon weg, dan blijft signal_power op zijn
    // laatste waarde staan en zou de trend "gelijk" melden terwijl er
    // helemaal niets meer gemeten wordt.
    const trendEl = document.getElementById('lora-trend');
    if (data.signal_detected) {
        toonTrend(trendEl, berekenTrend(data.signal_power, Date.now()));
    } else {
        resetTrend();
        toonTrend(trendEl, null);
    }

    // TIER 2: signaalgetrouwheid (SNR) + laatst ontvangen
    const snrEl = document.getElementById('lora-snr');
    const snrBarEl = document.getElementById('snr-bar-fill');
    const ageEl = document.getElementById('lora-last-seen');

    const snr = (typeof data.lora_snr === 'number') ? data.lora_snr : 0;

    // "Geen signaal" is geen SNR-waarde: leeg de balk en tekst i.p.v.
    // de laatst ontvangen waarde te laten hangen als de zender uit gaat.
    if (data.signal_detected) {
        const sign = snr >= 0 ? '+' : '';
        if (snrEl) snrEl.textContent = sign + snr.toFixed(1) + ' dB';
        if (snrBarEl) snrBarEl.style.width = snrToPercent(snr) + '%';
    } else {
        if (snrEl) snrEl.textContent = '-- dB';
        if (snrBarEl) snrBarEl.style.width = '0%';
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

    // TIER 2: laatst ontvangen packet-age
    if (ageEl) {
        const age = data.lora_last_seen_sec;
        ageEl.textContent = formatPacketAge(typeof age === 'number' ? age : -1);
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
window.snrToPercent = snrToPercent;