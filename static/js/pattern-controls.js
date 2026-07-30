/**
 * PATTERN-CONTROLS — beacon-nadering starten en volgen
 *
 * ONTWIKKELGEREEDSCHAP. Deze knop ijkt het zoekalgoritme; hij hoort niet
 * bij de werkstroom van de verdelger en mag weg zodra de drempels vaststaan.
 *
 * De knop startte eerder de rotatiemeting (pattern.py); die is afgerond en
 * de knop wijst nu naar de vooruit-nadering (approach.py). De rotatiecode
 * blijft in de codebase voor het geval hij terugkomt.
 *
 * Bevat:
 *  - showMetingModal()        toont de bevestigingsmodal
 *  - startMeting()            emit start_approach na bevestiging
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

// Terugvalhoogte als er geen radio-keuze gevonden wordt (bv. oude DOM).
// De server rondt opnieuw naar 2/3/4 m, dus dit is puur wat we vragen.
const METING_DEFAULT_HOOGTE = 3.0;


/** Lees de gekozen hoogte (2/3/4 m) uit de radio-buttons in de modal. */
function leesMetingHoogte() {
    const gekozen = document.querySelector('input[name="meting-hoogte"]:checked');
    const val = gekozen ? parseFloat(gekozen.value) : NaN;
    return isNaN(val) ? METING_DEFAULT_HOOGTE : val;
}


/** Toon de bevestigingsmodal. */
function showMetingModal() {
    const modal = document.getElementById('meting-modal');
    if (modal) modal.classList.add('show');
}


/**
 * Start de nadering. De modal heeft de operator al gewaarschuwd over de duur
 * en het feit dat de zender in de hand moet zijn, dus hier geen tweede
 * confirm() meer — die zou alleen maar wegklikgedrag aanleren.
 */
function startMeting() {
    if (!window.socket) return;
    const hoogte = leesMetingHoogte();
    hideModals();
    setMetingStatus('📊 Nadering starten...', '#f5a623');
    window.socket.emit('start_approach', { hoogte: hoogte });
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
 * Voortgangstekst = de servermelding. Zowel de rotatiemeting als de nadering
 * zetten per stap een informatieve message ("Meten op 5 m afgelegd (5/14)"),
 * dus we tonen die direct in plaats van hem client-side na te bouwen.
 */
function formatMetingVoortgang(data) {
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
