# Ontwerp: zoekalgoritme (search.py)

Zelfstandig ontwerpdocument voor het bouwen van `search.py`. Het vat de zes
meetvluchten en de ontwerpreview samen zodat een volgend gesprek kan bouwen
zonder de hele meetgeschiedenis te herhalen. Status: **ontwerp vastgelegd,
nog niet gebouwd.**

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

### Fase 1 — Peilen (continu)
Eén **continue** draai (niet 36 losse stops — die kostten bijna de hele accu).
De meetthread logt **elke packet met de heading op dat moment**. Ná de draai:
mediaan over hoekvensters (~15°) om uitschieters te filteren. **Kandidaten** =
de sterkste richting + alle richtingen binnen 2 dB daarvan.

Vereist de test-beacon op ~2 Hz (zie §4 / bandrand-commit). De **draaisnelheid
bepaalt de filtering**: bij 2 Hz krijg je `2 × (15/ω)` metingen per 15°-venster,
dus langzamer draaien = meer metingen = robuustere mediaan tegen de waargenomen
uitschieters. Drie opties (zie §4 en het budget in §6):

| Draai | Draaitijd | Metingen/15°-venster | Filtering |
|---|---|---|---|
| 3°/s | 120 s | 10 | best — maar krapste accumarge |
| **4°/s** | **90 s** | **~7-8** | **middenweg — aanbevolen** |
| 6°/s | 60 s | 5 | zwakst — alleen om accu te sparen |

**Aanbeveling: 4°/s.** 6°/s (5 metingen/venster) is te weinig voor een robuuste
mediaan gegeven de uitschieters; 3°/s filtert het best maar laat te weinig
accumarge voor een verworpen kandidaat (zie §6). Langzamer is altijd *veiliger*
(scherpere peiling); sneller alleen bewust om accu te sparen.

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

**Rotatiesnelheid fase 1 — de eerste instelbare parameter.** Bepaalt de
filtering én het accu-budget. Standaard **4°/s**; instelbaar op 3 / 4 / 6 °/s.
**Langzamer is altijd veiliger** (meer metingen per venster → scherpere
peiling); sneller kies je alleen bewust om accu te sparen. Telemetrie-skew
blijft klein genoeg bij alle drie (≤ 1,5° bij 6°/s, minder bij langzamer).

| Draai | Metingen/15°-venster | Draaitijd | Wanneer |
|---|---|---|---|
| 3 °/s | 10 | 120 s | beste filtering; alleen bij vertrouwen in een eerste-keer-raak kandidaat (krappe marge, §6) |
| **4 °/s** | ~7-8 | 90 s | **standaard — middenweg** |
| 6 °/s | 5 | 60 s | alleen om accu te sparen; zwakste filtering |

Overige parameters:

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
| 3. Naderen (15→6 m) | ~24 s |
| 4. Overvliegen | ~30 s |
| 5. RTL + landen | ~40 s |
| **Vast subtotaal** | **~124 s** |

Fase 1 (peilen) = draaitijd + ~10 s settle, en dus afhankelijk van de
rotatiesnelheid. Totaal en marge tot de 5,0 min-grens (300 s):

| Draai | Fase 1 | Vluchttotaal | Marge | Overleeft 1 verworpen kandidaat (~40 s)? |
|---|---|---|---|---|
| 3 °/s | 130 s | **254 s = 4,2 min** | 46 s (0,8 min) | **nee** — marge weg |
| **4 °/s** | 100 s | **224 s = 3,7 min** | 76 s (1,3 min) | **ja**, met ~36 s over |
| 6 °/s | 70 s | 194 s = 3,2 min | 106 s (1,8 min) | ja, ruim |

**Aanbeveling: 4°/s.** Het past met ~1,3 min marge en overleeft één verworpen
kandidaat. **3°/s filtert het best maar is te krap** — met 0,8 min marge blaast
één verworpen kandidaat het over de grens; gebruik het alleen als je een
eerste-keer-raak kandidaat verwacht (dichtbij, sterk contrast). 6°/s heeft de
meeste marge maar de zwakste peiling-filtering (§3).

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
- **NIET `pattern._verzamel_metingen`** — die meet **stilstaand** N samples.
  Fasen 1 en 4 hebben **continue** logging nodig (elk packet + heading/GPS
  tijdens beweging/draaien). Dat is een **nieuwe helper** in search.py; hergebruik
  het idee (nieuw packet via `status['lora_packet_count']`, waarden uit
  `status['signal_power']`/`['lora_snr']`, gekoppeld aan `status['heading']`/
  `['gps_lat']`/`['gps_lon']`), niet de functie.

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
- **Continue meting tijdens draaien (fase 1)** — haalt de heading-koppeling de
  ≤ 1,5° skew bij 6°/s? Verifieer de timing tussen LoRa-thread en mavlink-thread.
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
