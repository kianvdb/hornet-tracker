/**
 * PATTERN-CONTROLS — voortgang van meet- en zoekvluchten tonen
 *
 * Wat hier nog staat is de VOORTGANGSPLUMBING, geen startknop meer. De
 * knop "📊 Nadering" is weg: approach.py was ontwikkelgereedschap om het
 * zoekalgoritme te ijken, en dat ijken is klaar. De module approach.py
 * zelf blijft bestaan — search.py leent er de positie-staplogica van.
 *
 * Wat blijft:
 *  - registerMetingHandlers() luistert naar meting_update van de server
 *
 * meting_update is het gedeelde voortgangskanaal van pattern.py,
 * approach.py én search.py. De voortgang landt in #mission-status —
 * dezelfde plek als de missie en de servermeldingen. Eén meldingsplek: de
 * operator hoeft maar op één regel te letten, en de laatste melding is
 * altijd de actuele toestand.
 *
 * Afhankelijkheden:
 *  - window.socket            Socket.io client (gezet door main bootstrap)
 *  - DOM: #mission-status     gedeelde statusregel
 */


/**
 * Registreer de meting_update handler. De server stuurt bij elke stap een
 * update, zodat de operator tijdens de minuten die een vlucht duurt ziet
 * dat er iets gebeurt in plaats van naar een bevroren regel te kijken.
 */
function registerMetingHandlers() {
    if (!window.socket) return;
    window.socket.on('meting_update', function(data) {
        setMetingStatus(formatMetingVoortgang(data), kleurVoorStap(data.step));
    });
}


/**
 * Voortgangstekst = de servermelding. De zoekvlucht en de metingen zetten
 * per stap een informatieve message ("Fase 3/5 — naderen: stap 2, koers
 * 38°"), dus we tonen die direct in plaats van hem client-side na te bouwen.
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
window.registerMetingHandlers = registerMetingHandlers;
