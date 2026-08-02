# Ontwerp: zoekalgoritme (search.py)

Zelfstandig ontwerpdocument voor het bouwen van `search.py`. Het vat de zes
meetvluchten en de ontwerpreview samen zodat een volgend gesprek kan bouwen
zonder de hele meetgeschiedenis te herhalen. Status: **gebouwd**; fase 1 herzien na de
eerste veldvlucht (1-8-2026) van continu naar stapsgewijs.

---

## 1. Doel en scope

`search.py` laat de drone **autonoom de LoRa-beacon lokaliseren en de positie
loggen** (coördinaat-log-entry + dashboard-toast), zodat de verdelger naar het
nest kan.

**v1 is bewust KORTE AFSTAND (< 25 m) en één vlucht.** Reden: het principe eerst
valideren op een afstand die de metingen dekken, vóór de multi-vlucht-complexiteit
van echt veldzoeken (onbekende, grotere afstand). Zie §6 — op > 25 m past het
niet op één accu, en dat is een bewuste grens, geen omissie.

Zelfde patroon als `pattern.py` (rotatiemeting) en `approach.py` (nadering):
een knop op het dashboard start een thread; de zender-override blijft primair.

---

## 2. Wat de metingen vaststelden (het fundament)

Zes meetvluchten (2 m: logs 161/163, 4 m: logs 162/164, nadering: log 165, plus
de eerste 2 m op 24-7). Harde feiten waarop het ontwerp staat:

| Bevinding | Consequentie voor het ontwerp |
|---|---|
| **Rotatie-peiling geeft richting op ±30°**, reproduceert over 16,5 m verplaatsing en 2 m hoogteverschil (r=0,89 co-locatie) | Peiling werkt — maar de ±30° (= ±7,5 m op 15 m) moet later weggewerkt worden |
| **SNR verzadigt op ~15 m** (plateau +8 dB tot ~1,5 m) | SNR-magnitude geeft **geen** fijn-afstand; niet bruikbaar om "hoe ver nog" te bepalen |
| **RSSI stijgt van 15 → 6 m** (~7 dB), verzadigt daarna | RSSI is de **enige** afstandsmaat op middellange afstand |
| **Signaal-instort bij passeren** = beacon binnen ~1 m (SNR 6,8 → −0,5 over ~1 m) | De **enige** betrouwbare fijn-lokalisatie |
| **Body-frame `_cmd_forward` stapelde 33° heading-drift op** → 2,7 m scheef | Navigatie moet **positie-gebaseerd met vaste yaw**, niet body-frame-vooruit |
| **Beacon vs boomrand-reflectie: 2 dB verschil** in de rotatie | Alleen te scheiden **door beweging** (RSSI stijgt bij nadering van de echte bron, niet van de reflectie) |
| **Accu: ~1500 mAh/vlucht bruikbaar, ~5 min vluchttijd, één meting per lading** | Harde grens. Elke fase-seconde telt; hover kost ~290 mAh/min ongeacht wat je doet |

Detail dat het "hoogste piek"-idee onderuit haalt: op de 4 m-vlucht koos een
naïeve piek-keuze stap 32 (105°) terwijl de beacon op 30° lag — **76° fout**,
omdat de plateau-piek 0,3 dB boven de beacon-richting lag, binnen de ruis.

---

## 3. Het algoritme — 5 fasen

### Fase 1 — Peilen (STAPSGEWIJS, 12 × 30°)
Draai naar de hoek, **stop**, laat uitzweven, meet 5 packets stilstaand,
draai door. Twaalf stops op het vaste kompasraster 0, 30, 60 … 330°. Per hoek
de mediaan van SNR én RSSI. **Kandidaten** = de sterkste richting + alle
richtingen binnen 2 dB daarvan, geclusterd per lob.

**Waarom niet continu — dit is teruggedraaid, draai het niet terug.** Het
eerste ontwerp draaide in één vloeiende beweging rond (4°/s) en plakte elk
binnenkomend packet aan de heading van dat moment. Dat is op de eerste
veldvlucht (1-8) verlaten. De reden is structureel: meetwaarde en heading
komen uit verschillende bronnen met verschillende ververssnelheden — de
LoRa-ontvangstthread schrijft SNR en de packetteller meteen weg, de RSSI
wordt pas door `signal_loop` (10 Hz) bijgewerkt, en de heading komt via
`GLOBAL_POSITION_INT` op 4 Hz binnen. Zolang de drone draait betekent "een
ander tijdstip" ook "een andere hoek", en is niet te garanderen dat de drie
velden in één rij bij dezelfde richting horen.

Stilstaand meten haalt die vraag volledig weg: beweegt de drone niet, dan
maakt het niet uit of de heading 250 ms oud is. Geen kalibratie, geen
aanname over vertragingen, geen bewijslast.

Het is bovendien de methode van de **zes geslaagde rotatiemetingen**
(pattern.py, 36 × 10°). De continue variant was een accu-optimalisatie en is
de enige peiling die faalde. 30° in plaats van 10° houdt het op 12 stops —
een derde van de meettijd — en is nog steeds fijn genoeg tegenover de ±30°
peilnauwkeurigheid.

**Gemeten op de vlucht van 1-8:** de RSSI-kolom liep aantoonbaar één packet
achter op de SNR-kolom (lag-correlatie +0,88 bij −1 tegen +0,84 bij 0). Bij
3,7°/s is dat ~2° — klein, maar het is precies het soort koppeling dat met
stilstaand meten per definitie niet kan optreden.

### Fase 2 — Verifiëren
Per kandidaat: **5 m positie-gebaseerd** in die richting, RSSI vóór en ná.
**≥ 3 dB stijging = echte bron**; anders verwerpen, terugkeren, volgende
kandidaat. Zo scheidt beweging de echte bron van de boomrand-reflectie.

**OVERSLAAN als het startsignaal al sterk is** (drone < ~6 m van de bron, RSSI
al verzadigd): dan stijgt RSSI over 5 m geen 3 dB en zou verify de echte bron
onterecht verwerpen. Detecteer dit aan de absolute RSSI vóór de stap.

### Fase 3 — Naderen (RSSI-gradiënt, niet vaste bearing)
Dit is de **belangrijkste wijziging t.o.v. het eerste ontwerp.** Nader in
5 m-stappen, maar **corrigeer na elke stap de richting naar stijgende RSSI**
in plaats van blind de peil-bearing aan te houden. Dit:
- werkt de ±30° / ±7,5 m zijwaartse peilfout weg in dezelfde stappen (anders
  mist de overvlieg-pass de beacon lateraal — het zwakste punt van v0);
- scheidt nogmaals de echte bron van de reflectie (RSSI blijft stijgen richting
  de bron).

Stop bij **RSSI-verzadiging** (~6 m; RSSI stijgt niet meer over een stap).

### Fase 4 — Overvliegen
Eén **gladde pass**: positie-setpoint (`SET_POSITION_TARGET_GLOBAL_INT`) met
**vaste yaw**, tijdelijk `WPNAV_SPEED = 50` cm/s, tot ~5 m voorbij de geschatte
positie. **Continue meting** tijdens de pass. **Instort ACHTERAF** uit de
gelogde pass halen (steilste SNR-gradiënt), **niet live** — de pass voltooit
altijd, en de beacon-GPS komt uit de log met de tijdstempel (je hoeft er niet
live bij te zijn). Zie §3-review vraag 4.

### Fase 5 — Afronden
RTL naar het opstijgpunt, + coördinaat-log-entry op de gevonden positie +
dashboard-toast.

---

## 4. Parameters (met herkomst)

**Stapgrootte fase 1 — de eerste instelbare parameter.** Bepaalt de
hoekresolutie én het accubudget. Standaard **30°** (12 stops). Fijner meet
scherper maar kost lineair meer tijd; grover spaart accu maar kan twee
naburige lobben samentrekken.

| Stap | Stops | Fase 1 | Wanneer |
|---|---|---|---|
| 15° | 24 | ~155 s | te duur voor één accu |
| **30°** | **12** | **~78-93 s** | **standaard** |
| 45° | 8 | ~52 s | alleen om accu te sparen; kan lobben samentrekken |

Per stop: draai (30° bij 20°/s = 1,5 s) + heading-detectie (~1 s) + settle
(1,5 s) + 5 packets (2,5 s bij 2 Hz; 3,7 s bij het waargenomen 33%
pakketverlies) = **6,5-7,7 s**.

Overige parameters:Overige parameters:

| Parameter | Waarde | Herkomst |
|---|---|---|
| Kandidaat-marge | 2 dB | boomrand-reflectie lag 2 dB onder de beacon in de rotatie |
| Verificatiestap | 5 m | RSSI stijgt ~0,8 dB/m → 5 m geeft ~4 dB, ruim boven drempel |
| RSSI-verify-drempel | 3 dB | onder de ~4 dB verwachte stijging, boven de ruis (2,5 dB) |
| Verify-overslaan onder | ~6 m / hoge RSSI | RSSI verzadigt < 6 m |
| Naderingsstap | 5 m | zelfde als approach.py, positie-gebaseerd |
| Verzadigingsstop | ~6 m | RSSI stijgt niet meer onder ~6 m |
| Overvlieg-snelheid | 0,5 m/s (`WPNAV_SPEED=50`) | glad genoeg voor continue meting |
| Instort-drempel | > 5 dB daling over < 2 m | gemeten instort was 6,8 → −0,5 over ~1 m |
| Navigatie | positie-gebaseerd + **vaste yaw** | body-frame gaf 33° drift |
| Beacon-testfrequentie | 2 Hz (500 ms) | 10% duty bij ToA ~41 ms = max ~2,4 Hz; 2 Hz houdt marge |

**Bevestigde Pixhawk-params (uit log 165 / paramdump):** `WPNAV_SPEED=100`
(1 m/s), `WPNAV_SPEED_UP/DN=50`, `RTL_ALT=500` (5 m), `GUID_TIMEOUT=3.0`,
`BATT_LOW_VOLT=14.0`, `BATT_CRT_VOLT=13.6`, `FS_GCS_ENABLE=0`.

---

## 5. Faalgevallen → RTL overal

Elke fase is begrensd in tijd/afstand en valt bij falen terug op **RTL** met een
statusmelding die de reden noemt. **Nooit onbepaald LOITER** — dat verbrandt de
harde accugrens. De zender-override blijft altijd primair (mode ≠ GUIDED =
onmiddellijk stoppen, zoals in mission.py/pattern.py/approach.py).

- **Alle kandidaten verworpen** → RTL, meld welke bearings geprobeerd zijn.
- **RSSI stijgt nooit bij naderen** → je loopt niet naar een echte bron → stop, RTL.
- **Instort niet gevonden in de pass** → pass voltooit toch; log het beste-RSSI-punt
  als terugval met lage confidence, RTL.

---

## 6. Accu-budget

Hover ≈ 290 mAh/min → **harde grens ~5,0 min totale vluchttijd** (~1500 mAh
bruikbaar). Begroting bij een **nabije beacon (~15 m), eerste-keer-raak
kandidaat**, `WPNAV_SPEED=1` m/s. De **vaste** fasen (alles behalve peilen):

| Fase | Tijd |
|---|---|
| Takeoff + settle + arm | ~15 s |
| 2. Verifiëren (1 kandidaat) | ~15 s |
| — richtdraai vóór de nulmeting (2 kandidaten) | ~8 s |
| 3. Naderen (15→6 m) | ~24 s |
| 4. Overvliegen | ~30 s |
| 5. RTL + landen | ~40 s |
| — aankondiging volgende zet (~8 × 1,5 s) | ~12 s |
| **Vast subtotaal** | **~144 s** |

De richtdraai en de aankondiging zijn er ná de eerste veldvlucht bijgekomen:
de richtdraai omdat de nulmeting van fase 2 anders het antenne-effect van de
draai meemat, de aankondiging zodat de operator de volgende zet op de kaart
ziet vóór de drone vertrekt.

Fase 1 (peilen) = 12 stops × 6,5-7,7 s, afhankelijk van het pakketverlies:

| Fase 1 | Vluchttotaal | Marge tot 5,0 min | Overleeft 1 verworpen kandidaat (~40 s)? |
|---|---|---|---|
| 78 s (2 Hz, geen verlies) | **222 s = 3,7 min** | 78 s (1,3 min) | **ja** |
| 93 s (2 Hz, 33% verlies) | **237 s = 4,0 min** | 63 s (1,1 min) | **ja**, met ~23 s over |

**Het past, maar de marge is smal.** Op een accu die eerder leeg is dan de
begrote 5,0 min — bijvoorbeeld bij 4,5 min bruikbaar — blijft er bij 33%
pakketverlies nog 34 s over en past een verworpen kandidaat er **niet** meer
bij. `PEIL_MAX_DUUR_S` (150 s) is de harde noodrem: valt de beacon stil, dan
loopt elke meting in zijn packet-timeout en zou fase 1 anders de hele lading
opmaken voordat er één meter gevlogen is.

Het waargenomen pakketverlies van 33% op de vlucht van 1-8 is dus geen
detail: het kost ~15 s extra in fase 1. Minder verlies is de goedkoopste
manier om marge te winnen.

**Op > 25 m past geen enkele snelheid op één vlucht:** nadering en RTL schalen
met de afstand (op 40 m alleen al ~85 s nadering + ~50 s RTL). Verworpen
kandidaten kosten elk ~40 s. Daarom is **v1 korte afstand.**

**Toekomstig werk (buiten v1-scope):** het veldpad voor onbekende/grotere
afstand — multi-vlucht: peil → nader tot een afstandscap → markeer de
tussenpositie → accuwissel → hervat vanaf het markeerpunt. Vereist dat de drone
een tussenpositie kan onthouden/hervatten. Niet bouwen in v1.

---

## 7. Structuur en hergebruik

**Nieuw bestand `search.py`**, naast `pattern.py` en `approach.py`.

Hergebruik:
- **`mission.py`** — `_cmd_set_mode_guided`, `_cmd_arm`, `_cmd_takeoff`,
  `_cmd_land`, `_pilot_has_taken_over`, `_wait_with_pilot_check`
  (veiligheidsmodel + basiscommando's, ongewijzigd).
- **`approach.py`** — de positie-stap-logica (`_wacht_op_stap`) voor fasen 2-3,
  en het thread/state/`finally`-patroon (`start_meting` → thread → CSV/log bij
  afbreken).
- **`pattern._verzamel_metingen`** — stilstaand N packets met mediaan. Wordt
  gebruikt door fase 1, 2 én 3. Fase 1 gebruikte eerst een eigen continue
  helper; dat is teruggedraaid (zie §3).
- **Alleen fase 4 logt continu** (`_log_continu` in search.py): de pass is
  één doorlopende vlucht en de instort komt achteraf uit de reeks. Daar is
  de GPS-positie leidend en niet de hoek, dus de koppeling is minder
  gevoelig. Gebruik die helper niet opnieuw voor een peiling.

Nieuwe commando-helper nodig: **positie-setpoint met vaste yaw** (GLOBAL_INT of
LOCAL_NED met yaw-veld + type_mask) — bestaat nog niet in mission.py. Als de
meetmodules commando's gaan dupliceren, later een kleine `nav.py`; nog niet.

Dashboard: nieuwe knop + socket-event (bv. `start_search`), analoog aan
`start_approach` in app.py + pattern-controls.js. Voortgang via `meting_update`
naar `#mission-status` (bestaande meldingsplek).

---

## 8. Wat nog ongetest/onzeker is vóór de eerste vlucht

- **Instort-scherpte bij een GLADDE pass** — alleen schokkerig (stap-voor-stap)
  gemeten in approach.py. Bij continue lage snelheid kan de instort anders
  ogen. **Fase 4 is de minst-geverifieerde fase.**
- **De gradiënt-nadering (fase 3)** — oscilleert hij niet rond de richting bij
  ruis op de RSSI? Bouw met demping / minimale-stap-drempel.
- ~~Continue meting tijdens draaien (fase 1)~~ — **vervallen**: fase 1 meet
  stilstaand, dus er is geen heading-skew meer om te verifiëren.
- **Beacon-frequentie-afhankelijkheid** — de peiling-timing hangt aan 2 Hz; de
  **veldtracker zendt lager voor batterijduur** en haalt dat niet. In het veld
  wordt peilen trager, óf de tracker heeft een tijdelijke "zoekstand". Bewuste
  parameter, geen implementatiedetail.
- **Peiling-contrast bij te korte afstand (niet gemeten).** De rotatie-peiling
  werkte op ~15 m omdat de SNR daar nog per hoek varieerde (dip −2, piek +7).
  Maar de nadering liet zien dat SNR onder ~15 m verzadigt. Als de drone te
  dicht (< ~10 m) start, kan de SNR in álle richtingen verzadigd zijn → geen
  hoekcontrast → **fase 1 geeft dan geen richting.** Dit is niet direct gemeten
  (de nadering draaide niet), maar het maakt de v1-scope eerder een **bereik
  van ~10-25 m** dan alleen "< 25 m": niet te ver (accu), niet te dichtbij
  (peiling-contrast). Verifieer bij de eerste vlucht of de peiling contrast
  houdt op de gekozen startafstand.

Bouw fase 4 met **ruime marges en een duidelijke melding bij afwijkend gedrag**.

---

## 9. Openstaande beslissingen voor het bouw-gesprek

1. **Positie-setpoint-frame**: GLOBAL_INT (absolute GPS-doel) vs LOCAL_NED
   (relatief t.o.v. opstijgpunt) voor fasen 3-4. GLOBAL_INT is aanbevolen voor
   de overvlieg-pass (richt op een berekend GPS-punt), maar moet op de grond
   geverifieerd worden met vaste yaw (let op `WP_YAW_BEHAVIOR=2`).
2. **Gradiënt-correctie-regel (fase 3)**: hoeveel corrigeren per stap, en hoe
   ruis dempen (mediaan over N packets per meetpunt? minimale RSSI-stijging om
   te corrigeren?).
3. **Kandidaat-afhandeling**: hoeveel kandidaten maximaal proberen binnen het
   accubudget (elke verworpen kandidaat ~40 s)? Cap op 2?
4. **Instort-extractie (fase 4)**: exacte gradiënt-drempel en venster; hoe de
   GPS op het instortpunt uit de gelogde pass halen (interpolatie tussen
   samples).
5. **Beacon-positie-schatting** vóór fase 4: uit de gradiënt-nadering (laatste
   richting + verzadigingsafstand) — hoe robuust bij ±30° reststfout?
6. **CSV/log-output**: aparte `search_*.csv` met de peiling + nadering + pass,
   of alleen een coördinaat-log-entry? (Voor de thesis waarschijnlijk beide.)
7. **Verify-overslaan-drempel**: welke absolute RSSI geldt als "al te sterk"
   om te verifiëren (~−90 dBm?).
