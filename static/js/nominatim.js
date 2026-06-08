/**
 * NOMINATIM — gedeelde geocoding utility
 *
 * Bevat:
 *  - nominatimSearch(query)        forward geocoding (adres -> coords + display)
 *  - setupAddressAutocomplete(opts) hangs een autocomplete-flow op een input
 *
 * Achtergrond:
 *  Nominatim is OpenStreetMap's gratis forward/reverse-geocoder. Rate
 *  limit is 1 req/sec — we respecteren dat met een debounce (400ms na
 *  laatste toetsaanslag voor we de call doen). Bij meerdere consumers
 *  in het dashboard (tile-cache en map adres-zoek) zou een naieve
 *  implementatie de limit kunnen overschrijden; daarom is dit een
 *  gedeelde module met één gemeenschappelijke search-functie.
 *
 *  Voor de tile-prefetch en map-adres-zoek is het gedrag identiek
 *  (Nominatim API, België + buurlanden filter). Wat verschilt is wat
 *  er gebeurt na een klik op een suggestie — die callback is dus de
 *  enige parameter die per consumer wisselt.
 *
 * Gebruik:
 *
 *    setupAddressAutocomplete({
 *        inputEl:      document.getElementById('mijn-input'),
 *        suggestionEl: document.getElementById('mijn-suggesties'),
 *        onSelect:     (lat, lon, name) => { ... }
 *    });
 *
 *  De caller is verantwoordelijk voor de HTML-structuur en CSS van
 *  input + suggesties — deze module manipuleert alleen de inhoud.
 */


/**
 * Lage-niveau Nominatim search-call. Returns een array van resultaten
 * (mogelijk leeg), of throws bij netwerk-fout.
 *
 * @param {string} query     vrije tekst, bv. "Inkendaal Vlezenbeek"
 * @param {number} limit     max aantal resultaten (default 5)
 * @returns {Promise<Array>} array van Nominatim result objects
 */
async function nominatimSearch(query, limit = 5) {
    const url = `https://nominatim.openstreetmap.org/search?` +
        `q=${encodeURIComponent(query)}&` +
        `format=json&` +
        `limit=${limit}&` +
        `countrycodes=be,nl,fr,lu,de&` +
        `accept-language=nl`;

    const response = await fetch(url, {
        headers: { 'Accept': 'application/json' }
    });
    if (!response.ok) {
        throw new Error(`Nominatim HTTP ${response.status}`);
    }
    return await response.json();
}


/**
 * Hang een autocomplete-flow op een input-element.
 *
 * @param {Object} opts
 * @param {HTMLElement} opts.inputEl       het tekstveld waar operator typt
 * @param {HTMLElement} opts.suggestionEl  container voor suggesties
 * @param {Function}    opts.onSelect      callback (lat, lon, name) bij klik
 * @param {number}      [opts.debounceMs]  vertraging na laatste keypress
 * @param {number}      [opts.minChars]    min aantal chars voor we zoeken
 *
 * Roept opts.onSelect aan wanneer operator op een suggestie klikt. De
 * caller bepaalt wat er gebeurt — bv. de tile-cache modal vult lat/lon
 * velden in, de map-adres-zoek centreert de kaart.
 */
function setupAddressAutocomplete(opts) {
    const { inputEl, suggestionEl, onSelect } = opts;
    const debounceMs = opts.debounceMs || 400;
    const minChars = opts.minChars || 3;

    if (!inputEl || !suggestionEl || typeof onSelect !== 'function') {
        console.error('[nominatim] setupAddressAutocomplete: missing required opts');
        return;
    }

    let searchTimer = null;
    let lastQuery = '';

    inputEl.addEventListener('input', function() {
        const query = inputEl.value.trim();

        if (searchTimer) clearTimeout(searchTimer);

        if (query.length < minChars) {
            hideSuggestions();
            return;
        }
        if (query === lastQuery) return;

        showLoading();

        searchTimer = setTimeout(() => doSearch(query), debounceMs);
    });

    // Bij focus zonder typen: toon vorige suggesties opnieuw als er waren
    inputEl.addEventListener('focus', function() {
        const query = inputEl.value.trim();
        if (query.length >= minChars && query === lastQuery && suggestionEl.children.length > 0) {
            suggestionEl.classList.add('show');
        }
    });

    // Klik buiten = sluit suggesties
    document.addEventListener('click', function(e) {
        if (!e.target.closest('.address-search-container')
            && !suggestionEl.contains(e.target)
            && e.target !== inputEl) {
            hideSuggestions();
        }
    });

    async function doSearch(query) {
        lastQuery = query;
        try {
            const results = await nominatimSearch(query);

            // Race-condition: query is intussen weer veranderd?
            const currentQuery = inputEl.value.trim();
            if (currentQuery !== query) return;

            renderSuggestions(results);
        } catch (err) {
            console.error('[nominatim] zoeken mislukt:', err);
            suggestionEl.innerHTML =
                '<div class="address-empty">Zoeken mislukt — geen internet?</div>';
            suggestionEl.classList.add('show');
        }
    }

    function renderSuggestions(results) {
        if (!results || results.length === 0) {
            suggestionEl.innerHTML = '<div class="address-empty">Geen resultaten</div>';
            suggestionEl.classList.add('show');
            return;
        }

        // Bouw via DOM-API ipv innerHTML zodat we geen quote-escaping nodig
        // hebben voor onclick handlers — veiliger en cleaner.
        suggestionEl.innerHTML = '';

        for (const r of results) {
            const parts = r.display_name.split(',').map(s => s.trim());
            const mainName = parts[0] || r.display_name;
            const detail = parts.slice(1, 4).join(', ');
            const lat = parseFloat(r.lat);
            const lon = parseFloat(r.lon);

            const item = document.createElement('div');
            item.className = 'address-suggestion';

            const mainEl = document.createElement('div');
            mainEl.className = 'addr-main';
            mainEl.textContent = mainName;

            const detailEl = document.createElement('div');
            detailEl.className = 'addr-detail';
            detailEl.textContent = detail;

            item.appendChild(mainEl);
            item.appendChild(detailEl);

            item.addEventListener('click', function() {
                hideSuggestions();
                onSelect(lat, lon, mainName);
            });

            suggestionEl.appendChild(item);
        }
        suggestionEl.classList.add('show');
    }

    function showLoading() {
        suggestionEl.innerHTML = '<div class="address-loading">Zoeken...</div>';
        suggestionEl.classList.add('show');
    }

    function hideSuggestions() {
        suggestionEl.classList.remove('show');
    }

    // Expose hide voor expliciete reset vanuit caller
    return { hideSuggestions };
}


// Expose op window — geen build-step in dit project
window.nominatimSearch          = nominatimSearch;
window.setupAddressAutocomplete = setupAddressAutocomplete;
