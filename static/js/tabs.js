/**
 * TABS — wisselen tussen de panelen in de linkerkolom
 *
 * De kaart in de rechterkolom blijft altijd zichtbaar; alleen links
 * wisselt tussen Veld (signaal + besturing), Thermisch en Log.
 *
 * Waarom tabs en geen aparte pagina's: een echte navigatie zou de
 * socket verbreken en de kaart opnieuw initialiseren — trail weg,
 * marker weg, opnieuw wachten op fix. In het veld gaat de verdelger
 * heen en weer tussen kaart en log; dat moet naadloos zijn.
 *
 * De actieve tab wordt geëxposeerd via getActiveTab() zodat andere
 * modules onnodig renderwerk kunnen overslaan (zie thermal-display.js).
 */

const TAB_IDS = ['veld', 'thermal', 'log'];
let activeTab = 'veld';

/**
 * Wissel naar een paneel. Onbekende namen worden genegeerd zodat een
 * typfout in een onclick geen lege kolom oplevert.
 */
function switchTab(name) {
    if (!TAB_IDS.includes(name)) {
        console.warn(`[tabs] onbekende tab: ${name}`);
        return;
    }

    activeTab = name;

    TAB_IDS.forEach(function(id) {
        const btn   = document.getElementById('tab-' + id);
        const panel = document.getElementById('panel-' + id);
        const isActive = (id === name);

        if (btn) {
            btn.classList.toggle('active', isActive);
            btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
        }
        if (panel) {
            panel.classList.toggle('active', isActive);
        }
    });
}

/** Welke tab staat open — gebruikt om renderwerk over te slaan. */
function getActiveTab() {
    return activeTab;
}

window.switchTab   = switchTab;
window.getActiveTab = getActiveTab;
