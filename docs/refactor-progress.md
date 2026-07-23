## Uitgesteld: manual-arm cleanup

Kandidaten voor verwijdering, wachtend tot het zoekalgoritme klaar is
(mogelijk heeft dat setMode of iets vergelijkbaars nodig):

- `armDrone()` / `disarmDrone()` — knoppen verdwenen in de
  twee-knoppen-vereenvoudiging, alleen nog op window geëxposeerd
- `setMode()` — geen aanroeper meer
- `updateModeButtons()` — nog aangeroepen vanuit socket-handlers.js,
  maar `.mode-btn` bestaat nergens in de HTML: no-op op lege NodeList
- `armCommandTimeout` — 4s-reset uitgecommentarieerd, kan helemaal weg
- `#arm-modal` + `confirmArm()` — modal is nog bruikbaar voor grondtest
  met props eraf; alleen verwijderen als dat definitief niet meer nodig is
- De color-string-aanroepen van `setFeedback()` in bovenstaande paden
  (2e argument is nu een boolean; oude strings vallen door naar grijs)
