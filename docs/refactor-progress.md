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

## Openstaand: dubbele klik bij missie- en metingstart

Symptoom: START MISSIE en START METING vereisen twee klikken. De eerste
klik geeft "piloot heeft overgenomen", de tweede werkt zonder verdere
handeling.

Diagnose (bevestigd, niet gegokt): de zender werd niet aangeraakt — de
dataflash-log van de mislukte 2m-vlucht toonde de SwC constant. Het is
een timing-race: bij de start wordt GUIDED gecommandeerd, maar de
mode-check (_pilot_has_taken_over) gebeurt voordat de Pixhawk de wissel
heeft bevestigd. status['flight_mode'] staat dan nog op de oude mode,
de check ziet "niet GUIDED" en breekt af. Bij de tweede klik staat de
mode al op GUIDED.

Raakt zowel mission.py als pattern.py — beide zetten eerst GUIDED en
checken daarna.

Voorgestelde fix: na _cmd_set_mode_guided() actief wachten tot
status['flight_mode'] == 'GUIDED' (met timeout) voordat de
overname-detectie begint, zoals change_mode() in app.py al doet. De
echte overname-detectie tijdens de vlucht mag hier niet door verzwakken.

Status: gediagnosticeerd, nog niet gefixt. Doen vóór de demo — twee keer
moeten klikken oogt onbetrouwbaar bij de verdediging.
