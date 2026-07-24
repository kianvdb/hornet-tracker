/**
 * PATTERN-CONTROLS — stralingsdiagram-meting starten en volgen
 *
 * ONTWIKKELGEREEDSCHAP. Deze knop ijkt het zoekalgoritme; hij hoort niet
 * bij de werkstroom van de verdelger en mag weg zodra de drempels vaststaan.
 *
 * Bevat:
 *  - showMetingModal()        toont de bevestigingsmodal
 *  - startMeting()            emit start_meting na bevestiging
 *  - registerMetingHandlers() luistert naar meting_update van de server
 *
 * De voortgang landt in #mission-status — dezelfde plek als de missie en de
 * servermeldingen. Eén meldingsplek: de operator hoeft maar op één regel te
 * letten, en de laatste melding is altijd de actuele toestand.
 *
 * Afhankelijkheden:
 *  - window.socket            Socket.io client (gezet door main bootstrap)
 *  - DOM: #meting-modal       bevestigingsmodal
 *  - reuse: hideModals()      uit modals.js
 */

// Hoogtes van de twee meetrondes. Server clampt opnieuw (1,5–5 m), dus dit
// is puur wat we vragen, geen veiligheidsgrens.
const METING_HOOGTES = [2.0, 4.0];


/** Toon de bevestigingsmodal. */
function showMetingModal() {
    const modal = document.getElementById('meting-modal');
    if (modal) modal.classList.add('show');
}


/**
 * Start de meting. De modal heeft de operator al gewaarschuwd over de duur
 * en het feit dat de zender in de hand moet zijn, dus hier geen tweede
 * confirm() meer — die zou alleen maar wegklikgedrag aanleren.
 */
function startMeting() {
    if (!window.socket) return;
    hideModals();
    setMetingStatus('📊 Meting starten...', '#f5a623');
    window.socket.emit('start_meting', { hoogtes: METING_HOOGTES });
}


/**
 * Registreer de meting_update handler. De server stuurt bij elke hoekstap
 * een update, zodat de operator tijdens de ~4 minuten per ronde ziet dat er
 * iets gebeurt in plaats van naar een bevroren regel te kijken.
 */
function registerMetingHandlers() {
    if (!window.socket) return;
    window.socket.on('meting_update', function(data) {
        setMetingStatus(formatMetingVoortgang(data), kleurVoorStap(data.step));
    });
}


/**
 * Bouw de voortgangstekst. Tijdens het meten tonen we hoogte + stap, want
 * dat is het enige dat verandert; bij alle andere stappen is het
 * servermeldingsveld informatiever.
 */
function formatMetingVoortgang(data) {
    if (data.step === 'meten' && data.hoogte !== null && data.stap) {
        // Nederlandse decimale komma: "2,0 m" leest natuurlijker dan "2.0 m"
        const hoogte = data.hoogte.toFixed(1).replace('.', ',');
        return `Meting ${hoogte} m — stap ${data.stap}/${data.totaal_stappen}`;
    }
    return data.message || 'Meting bezig...';
}


/** Kleur per stap: rood bij fout/overname, groen bij klaar, oranje tijdens. */
function kleurVoorStap(step) {
    if (step === 'fout' || step === 'gestopt') return '#f87171';
    if (step === 'klaar') return '#4ade80';
    return '#f5a623';
}


/**
 * Schrijf naar de gedeelde statusbalk. Guard omdat #mission-status in de
 * besturing-card zit, die bij de tab-split verborgen kan zijn — verborgen
 * is prima, afwezig zou hier crashen.
 */
function setMetingStatus(text, color) {
    const el = document.getElementById('mission-status');
    if (!el) return;
    el.textContent = text;
    el.style.color = color;
}


// Expose op window
window.showMetingModal        = showMetingModal;
window.startMeting            = startMeting;
window.registerMetingHandlers = registerMetingHandlers;
