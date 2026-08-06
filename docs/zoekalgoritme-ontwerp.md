# Ontwerp: zoekalgoritme (search.py)

Zelfstandig ontwerpdocument voor het bouwen van `search.py`. Het vat de zes
meetvluchten en de ontwerpreview samen zodat een volgend gesprek kan bouwen
zonder de hele meetgeschiedenis te herhalen. Status: **gebouwd en vijf keer gevlogen**; fasen 1, 3 en 4 zijn
herzien op grond van die vluchten — zie §9.

> **Het algoritme is af; het toestel niet.** Sinds de val van 2-8 keurt de
> hovertest elke vlucht af op een draaikoppel dat na vijf inspecties niet
> gelokaliseerd is. Er wordt opnieuw gebouwd met behoud van de radioketen —
> zie §8. Verder sleutelen aan `search.py` heeft tot die tijd geen zin.

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

## 3. Het algoritme — hovertest + 5 fasen

### Fase 0 — Hovertest (toestandscontrole)
Na het opstijgen, op zoekhoogte en nog boven het opstijgpunt: 5 s stilhangen
en uitlezen wat de vluchtcontroller zelf al meet. Deugt het niet, dan **LAND**
— hier, meteen, zonder één meter te vliegen. Bewust landen en niet RTL: bij
een mechanisch probleem is recht naar beneden op een bekende plek veiliger
dan eerst ergens heen vliegen.

**Waarom deze fase bestaat.** Op 2-8 21:26 viel de drone recht naar beneden,
vlak na de richtingbepaling. Uit het .BIN:

```
  t-4,6s   trilling 26    roll +1,6/+1,2   pitch -3,2/-3,4   rustige hover
  t-4,4s   trilling 38    roll +1,6/+1,2   pitch -3,4/-4,1   trilling loopt op
  t-4,0s   trilling 68    roll -0,6/+1,1   pitch -15,1/-12,0 stand loopt weg
  t-3,4s   trilling 73    roll -91,6/+19,7                   op zijn kant
  t 0      Crash: Disarming: AngErr=54>30, Accel=0,7<3,0
```

**De trilling gaat als eerste omhoog, pas daarna verliest de regeling het.**
Motor C1 stond vastgepind op zijn minimum terwijl C4 op maximum stond en de
hoogteregelaar 100% gas vroeg — verlies van stuwkracht aan één kant. De
EKF- en kompasmeldingen in de log kwamen 1,5 tot 3 s later en waren gevolg,
geen oorzaak: het magnetisch veld was vlak (502-521), GPS had 26-28
satellieten met HDop 0,51, en de accu zakte alleen onder de 29,7 A belasting.

Wat al vóór de val zichtbaar was: de vier motoren stonden **103 PWM uit
elkaar** tijdens rustig stilhangen (1623 / 1708 / 1725 / 1626). Een quad in
balans zit binnen enkele tientallen. Die scheefstand stond vanaf minuut één
in de log — er keek alleen niemand naar.

| grootheid | bron | waarschuwing | afkeuren |
|---|---|---|---|
| trilling (max X/Y/Z) | `VIBRATION` | > 30 | **> 60** |
| spreiding tussen de 4 motoren | `SERVO_OUTPUT_RAW` | > 140 PWM | **> 200 PWM** |
| clipping versnellingsmeter | `VIBRATION` | — | **elke toename** |
| ongevraagde draai tijdens de klim | `GLOBAL_POSITION_INT` heading | > 15° | **> 30°** |

De gemeten waarden gaan altijd de CSV-kop in, ook als de test slaagt.

**Eerst de kanaaltoewijzing — zonder deze tabel leest elke log verkeerd.**
De ESC's zitten op dit toestel niet in de standaardvolgorde op de Pixhawk;
dat is rechtgezet met `SERVOn_FUNCTION` na de allereerste crash (33 = motor 1,
34 = motor 2, 35 = motor 3, 36 = motor 4). De **logkolommen `RCOU.C1..C4` zijn
dus niet motor 1 tot 4.** Bevestigd tegen de bedrading van de operator:

| logkolom | ArduPilot-motor | positie | draairichting |
|---|---|---|---|
| `C1` | motor 4 | rechtsachter | CW |
| `C2` | motor 1 | rechtsvoor | CCW |
| `C3` | motor 2 | linksachter | CCW |
| `C4` | motor 3 | linksvoor | CW |

Ontleding per as, als half verschil zodat het per motor leest:

```
  rol   = ((C1 + C2) − (C3 + C4)) / 2      rechts − links
  pitch = ((C2 + C4) − (C1 + C3)) / 2      voor  − achter
  yaw   = ((C2 + C3) − (C1 + C4)) / 2      CCW   − CW
```

**Basislijn van dít toestel**, per vlucht, in een venster van 30 s rustig
hangen na de klim:

| log | vlucht | spreiding | rol | pitch | yaw | trilling |
|---|---|---|---|---|---|---|
| 161 | 24-7 rotatie | 88 PWM | −8 | +3 | **+80** | 18,4 |
| 163 | 28-7 rotatie | 110 PWM | +6 | −18 | **+92** | 20,9 |
| 165 | 29-7 nadering | 100 PWM | −20 | −27 | **+73** | 17,2 |
| 166 | 1-8 zoek | 119 PWM | +26 | −12 | **+94** | 32,2 |
| 167 | 2-8 zoek | 94 PWM | −5 | −5 | **+89** | 16,5 |
| 169 | 2-8 zoek ok | 106 PWM | −11 | −12 | **+94** | 19,0 |
| 170 | 2-8 val | 128 PWM | −34 | +24 | **+93** | 31,2 |
| **176** | **3-8 na reparatie** | **420 PWM** | −27 | −53 | **−367** | **32,1** |
| **177a** | **3-8 na reparatie** | **334 PWM** | −18 | +19 | **−315** | **26,3** |
| **177b** | **3-8 na reparatie** | **417 PWM** | −33 | −71 | **−346** | **39,9** |
| **179** | **4-8 na 2e ESC-reparatie** | **325 PWM** | +8 | −7 | **−317** | **26,0** |

**Deze quad hangt normaal op 88-128 PWM spreiding — ook op de zes geslaagde
meetvluchten.** Een eerdere lezing dat de 103 PWM vóór de val al een
waarschuwing was, klopte niet: dat is gewoon de basislijn, en een grens van
80 zou elke vlucht uit dit project hebben tegengehouden.

De vaste scheefstand zit in de **yaw-as**: op alle zeven vluchten vóór de val
liepen de CCW-motoren 73-94 PWM harder dan de CW-motoren. Een kleine,
onschadelijke draaitrim van deze bouw. **De rol-as is over alle elf de
vluchten in balans** (−34 tot +26), vóór én na de val.

Na de val klapte de yaw-as om naar −315 tot −367: nu lopen de CW-motoren ruim
300 PWM harder. De verandering zit vrijwel volledig in die ene as (~440 PWM),
tegen ~60 in pitch en nul in rol.

> **Een eerdere lezing van deze tabel was fout en is hier rechtgezet.** Door
> `C1..C4` als motor 1 tot 4 te lezen, kwam de scheefstand in de ROL-as
> terecht en leek er massa naar rechts verschoven te zijn. Dat klopt niet:
> een zwaartepuntverschuiving kan fysiek **geen** draaikoppel om de verticale
> as maken, en de rol-as is onveranderd. Balanceren op twee vingers toetst
> een as die nooit bewogen heeft.

**Ongevraagde draai tijdens de klim** is géén tweede verschijnsel maar
hetzelfde, zichtbaar in de fase waarin de regelaar nog niet bijgebeend is. Op
vlucht 176 draaide de drone 147° om zijn as tussen t=3 s en t=8 s, terwijl de
zeven eerdere vluchten binnen 1-7° bleven:

| log | 161 | 163 | 165 | 166 | 167 | 169 | 170 | **176** | **179** |
|---|---|---|---|---|---|---|---|---|---|
| drift tijdens klim | −2° | −5° | −6° | −1° | −6° | −7° | −7° | **+132°** | **+82°** |

Vier keer hetzelfde beeld na de val, dus reproduceerbaar en geen ruis.

Dat is geen schattingsfout: de gyroscoop mat +147° en de EKF +159°, dus
fysiek gedraaid. Wat de log laat zien is dat de drone **al draaiend loskomt**
— +27 tot +36 °/s op het moment van opstijgen, tegen −7 tot +8 op de gezonde
vluchten — en dat de yaw-regelaar ongeveer 4,5 s nodig heeft om die draai te
stoppen. In die 4,5 s veegt hij de 60 tot 130° die de test meet. Zodra hij
stilhangt houdt hij de koers wel, want dan heeft hij de tijd.

De regelaar betaalt daar permanent voor: zijn yaw-uitgang staat op −0,48 tot
−0,51 waar hij op de gezonde vluchten op +0,11 tot +0,14 stond, en de
integrator komt in evenwicht op ~0,5 — precies zijn `ATC_RAT_YAW_IMAX`. Er is
dus geen reserve meer voor een windvlaag of een draaicommando. De twee
CW-motoren hangen bovendien op 78% van hun bereik tegen 59% normaal, wat meer
dan de helft van de bovenmarge voor rol- en pitchcorrecties wegneemt.

De eerdere verklaring — "motoren die later aanslaan breken de koppelbalans
tijdens de klim" — verklaart de startdrempel maar niet dit: het draaikoppel
staat er ook in stabiele zweefvlucht, en het verdwijnt niet bij constant gas.

**Daarom wordt dit tijdens de KLIM gemeten en niet tijdens het stilhangen.**
De heading wordt bijgehouden vanaf het takeoff-commando tot de hoogte bereikt
is; het resultaat gaat mee in het oordeel van de hovertest. Niemand
commandeert daar yaw — ArduCopter niet en search.py niet — dus alles wat
gemeten wordt, is de drone die zichzelf verdraait.

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

**Draai via de KORTSTE weg (2-8).** `pattern._cmd_yaw_absoluut` heeft de
draairichting hardgecodeerd op "met de klok mee". Voor pattern.py is dat
onschadelijk (stappen van +10° lopen altijd vooruit), maar hier ligt de
eerste rasterhoek willekeurig ten opzichte van de stand na het opstijgen.
Op alle drie de veldvluchten haalde de eerste stap zijn hoek daardoor niet
binnen de timeout en werd hij **midden in de draai** gemeten:

| vlucht | commando | gemeten op |
|---|---|---|
| 1-8 21:05 | 0° | 75,3° |
| 2-8 17:18 | 0° | 91,7° |
| 2-8 19:12 | 0° | 71,1° |

Tijdens die vijf packets draait de drone ~60° door, dus zo'n meetwaarde
hoort bij geen enkele hoek. `search._cmd_yaw_kortste` rekent de richting nu
zelf uit en geeft ±1 expliciet mee (niet 0 = "kortste", zodat het gedrag
niet afhangt van de firmware-interpretatie). De timeout is 15 s: met de
kortste weg is 180° de grootste draai, en dat is 9 s bij 20°/s.

**Een niet-bereikte hoek wordt OVERGESLAGEN, niet gemeten.** Een gat in het
diagram is eerlijker dan een punt dat bij geen enkele hoek hoort; het
zwaartepunt kan tegen een ontbrekende hoek, niet tegen een verkeerd
gelabelde.

**Kandidaatkeuze: het ZWAARTEPUNT, niet de sterkste hoek.** De sterkste hoek
nemen faalde op twee vluchten met 76° en 84°. De hoofdlob is ~150-180° breed
en de variatie daarbinnen (~1 dB) is even groot als de ruis op een mediaan
van 5 packets. Het zwaartepunt van dezelfde meetreeks wijst wél naar de bron:

| vlucht | beacon | piek | zwaartepunt |
|---|---|---|---|
| 1-8 17:38 | 133,6° | 217,5° (84° eraf) | 131,9° (**1,8° eraf**) |
| 1-8 21:05 | 132,6° | 208,9° (76° eraf) | 126,9° (**5,7° eraf**) |
| 2-8 19:12 | 133,0° | 151,9° (19° eraf) | 144,5° (**11,5° eraf**) |

Weging: `10^((snr − snr_min)/10)`, lineair vermogen ten opzichte van de
zwakste gemeten richting. Acht alternatieve schatters zijn tegen dezelfde
drie vluchten getoetst (RSSI in plaats van SNR, drempels op 3/4/6 dB,
kwadratische en derdemachtsweging, sectorweging) — geen enkele verslaat deze
betrouwbaar. De piek blijft als TWEEDE kandidaat bestaan wanneer hij meer
dan 45° van het zwaartepunt ligt: dan zijn het twee echt verschillende
hypotheses en beslist fase 2 ertussen.

**Elke vlucht rapporteert zijn eigen lobkwaliteit** (`_lob_diagnose`):
breedte op 3 dB, variatie erbinnen, en piek-versus-zwaartepunt. Dat kost
geen vliegtijd en maakt van elke zoekvlucht meteen een antennepatroon-meting
— schoner dan een grondtest, want daar staat de operator zelf in het
stralingspatroon.

### Fase 2 — Verifiëren
Neus op de kandidaatrichting, **uitzweven**, RSSI meten, **10 m** positie-
gebaseerd in die richting, opnieuw meten. **≥ 3 dB stijging = echte bron**;
anders verwerpen, terugkeren, volgende kandidaat.

**Eerst draaien, dan pas de nulmeting.** Meet je vóór de draai, dan zit het
antenne-effect (tot 9 dB) in het voor/na-verschil en wordt elke kandidaat
"bevestigd", ook een reflectie.

**De stap was 5 m en dat kon rekenkundig niet werken.** Padverlies loopt met
20·log10(d2/d1), dus de winst hangt van de AFSTAND af, niet van een vaste
dB/m. De oude onderbouwing ("RSSI stijgt ~0,8 dB/m, dus 5 m geeft ~4 dB") nam
een helling die alleen rond 11 m geldt en behandelde die als constante:

| afstand | stap 5 m | stap 10 m |
|---|---|---|
| 15 m | 3,52 dB | 9,54 dB |
| 18 m | **2,83 dB** | **7,04 dB** |
| 21 m | 2,36 dB | 5,62 dB |
| 25 m | 1,94 dB | 4,44 dB |

Met de drempel op 3 dB kon vanaf ~17 m **geen enkele kandidaat slagen, ook
de juiste niet**. Precies dat gebeurde twee vluchten op rij. Met 10 m wordt
bovendien een slordige peiling verdragen: op 18 m haalt een peilfout tot
**±43°** nog +3 dB, tegen géén enkele fout bij 5 m.

**OVERSLAAN als het startsignaal al sterk is** (RSSI ≥ −88 dBm, ~7-8 m): dan
is de RSSI verzadigd en zou verify de echte bron onterecht verwerpen.

### Fase 3 — Doorvliegen (één doorlopende vlucht)
Neus op de bevestigde koers, **volledig uitgedraaid**, dan één doorlopende
vlucht op 0,5 m/s met continue meting, dwars over de bron heen. Stopt zodra
de RSSI 6 dB onder het sterkste punt van deze vlucht is gezakt én we er 5 m
voorbij zijn, of bij 20 m. Die live-stop begrenst alleen de VLUCHT; de
positie komt achteraf uit de log.

**Dit verving de stapsgewijze nadering met gradiënt-correctie.** Wat daarmee
gebeurde op 2-8 17:18: de drone stond na twee stappen op **2,70 m** van de
beacon met het sterkste signaal van de vlucht (−81 dBm) en vloog er
vervolgens **21 meter vandaan**. De gradiëntlogica onthield alleen de beste
WAARDE, niet de beste PLEK, en kon "ik loop ernaast" niet onderscheiden van
"ik ben er net overheen" — beide geven een dalende RSSI. Drie koers-
correcties later was het stappenmaximum op en begon de pass vanaf het
slechtste punt.

Waarom doorvliegen nu kan:
- het zwaartepunt peilt op 2-12°, en bij 17 m passeer je dan op 0,6-3,9 m
  van de bron — binnen het bereik waarop de instort zichtbaar is
- continu meten op 0,5 m/s bij 2 Hz geeft een sample elke 0,25 m, tegen één
  punt per 5 m bij stappen
- de instort is een veel sterker signaal (>5 dB over 1-3 m) dan de gradiënt
  tussen twee stappen (~1,5 dB, gelijk aan de ruis)
- het is sneller: ~30 s in plaats van ~70 s

Wat we inleveren is de correctie onderweg. Dat is een bewuste ruil — de
correctie werkte aantoonbaar averechts — en de terugkruising vangt het op.

**De yaw moet vóór het loggen uitgedraaid zijn.** Op 2-8 17:18 begon de pass
terwijl de yaw van 103° naar 143° draaide; de eerste vijf samples gaven SNR
+6,0 → +1,2 → 0,0. Dat was de antenne die wegdraaide. Op die reeks zou de
instort-extractie een positie hebben gelogd met betrouwbaarheid **HOOG** die
**20,9 m** naast de beacon lag. Een fout-positief met hoge zekerheid is het
gevaarlijkste dat dit systeem kan produceren.

### Fase 4 — Terugkruising
180° draaien, uitzweven, dezelfde lijn terug meten. Levert een **tweede**
instort op, uit tegengestelde richting.

Waarom dat meer is dan een herkansing: elke systematische vertraging in de
meetketen schuift de geschatte instortpositie een vast aantal meters
VOORUIT langs de vliegrichting. Draai je om, dan schuift die fout precies de
andere kant op. Het **middelpunt** van de twee kruisingen is er dus vrij van,
zonder dat we de vertraging hoeven te kennen. Getest op gesimuleerde
doorvluchten:

| zijwaartse misser | heen | terug | middelpunt |
|---|---|---|---|
| 0 m | 2,54 m | 2,14 m | **0,20 m** |
| 2 m | 2,53 m | 2,31 m | **0,11 m** |
| 3 m | 2,58 m | 2,19 m | **0,20 m** |

De terugweg is bovendien grotendeels de thuisreis.

**Altijd middelen, ook bij een groot verschil.** Eerst weigerde de code te
middelen bij meer dan 6 m verschil en koos ze er één. Op 2-8 19:12 pakte dat
slecht uit: de twee schattingen lagen 8,0 m uit elkaar, maar hun middelpunt
lag **3,69 m** van de beacon terwijl de gekozen enkeling er **5,23 m** naast
zat. De spreiding is informatie over de ONZEKERHEID, geen reden om data weg
te gooien; hij bepaalt daarom de betrouwbaarheid:

| | |
|---|---|
| beide een echte instort, < 3 m uit elkaar | hoog |
| < 6 m uit elkaar | midden |
| verder uit elkaar | laag |
| gemengd (instort + RSSI-terugval) | hoogstens midden |

### Fase 5 — Afronden
Middelpunt van de twee kruisingen, terug naar het opstijgpunt, landen,
coördinaat-log-entry en toast.

**Terug op zoekhoogte, niet klimmend naar `RTL_ALT`.** Standaard klimt een
RTL eerst naar 5 m. Dat levert niets op: de drone komt van een punt dat hij
zelf net overvlogen heeft, dus die route is vrij. Lager terugkomen scheelt
accu en beperkt de val als er onderweg alsnog iets bezwijkt. `RTL_ALT` gaat
daarom vlak vóór de terugvlucht op de zoekhoogte en wordt in het
`finally`-blok teruggezet op 500 cm.

Bewust niet `RTL_ALT = 0` ("huidige hoogte"): mislukt het terugzetten, dan
klimt een RTL van de piloot nog altijd naar de zoekhoogte in plaats van vlak
over de grond terug te vliegen.

De gevonden positie gaat server-side naar de coördinaat-log, met de
betrouwbaarheid en de volledige onderbouwing in de notitie — zodat de
verdelger ziet of hij naar een coördinaat kijkt of naar een richtinggever.

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

Overige parameters:

| Parameter | Waarde | Herkomst |
|---|---|---|
| Metingen per hoek | 5 packets | mediaan; ruis op het verschil tussen twee hoeken ~1 dB |
| Heading-timeout fase 1 | 15 s | 180° bij 20°/s = 9 s, plus marge |
| Kandidaatkeuze | zwaartepunt op SNR | piek faalde met 76° en 84° op twee vluchten |
| Piek als 2e kandidaat vanaf | 45° van het zwaartepunt | daaronder zeggen ze hetzelfde |
| Max kandidaten | 2 | elke verworpen kandidaat kost ~50 s |
| **Verificatiestap** | **10 m** | 5 m haalt de 3 dB-drempel niet voorbij 17 m |
| RSSI-verify-drempel | 3 dB | boven de ruis (2,5 dB) |
| Verify-overslaan boven | −88 dBm (~7-8 m) | RSSI verzadigt < 6 m |
| Doorvliegen: snelheid | 0,5 m/s (`WPNAV_SPEED=50`) | sample elke 0,25 m bij 2 Hz |
| Doorvliegen: max afstand | 20 m | startafstand 25 m − 10 m verify + 5 m voorbij |
| Doorvliegen: voorbij de instort | 5 m | genoeg om de daling volledig te loggen |
| Live-stopdrempel | 6 dB onder de top | begrenst alleen de vlucht, niet de schatting |
| Yaw uitzweven vóór meten | 3 s | zonder dit meet je je eigen draai (2-8 17:18) |
| Instort-drempel | 4 dB over 3 m | ">5 dB over <2 m" vuurt niet op log 165 |
| Instort zwak (nog bruikbaar) | 2 dB | steilste punt ligt dan nog dichter bij dan sterkste RSSI |
| Navigatie | `LOCAL_OFFSET_NED` + vaste yaw | fout kost hooguit één stapgrootte, niet een duik |
| Beacon-testfrequentie | 2 Hz (500 ms) | 10% duty bij ToA ~41 ms = max ~2,4 Hz |
| Hovertest: duur | 5 s | ~20 monsters bij 4 Hz telemetrie |
| Hovertest: trilling afkeuren | > 60 | ArduPilot-richtlijn; bij de val 26 vóór en 114 tijdens |
| Hovertest: motoren afkeuren | > 150 PWM | bij de val 103 PWM tijdens rustig hangen |
| Hovertest: clipping | elke toename | dan is de stand- en hoogteschatting onbetrouwbaar |
| RTL-hoogte fase 5 | = zoekhoogte | geen klim naar 5 m; teruggezet op 500 cm in `finally` |

**Bevestigde Pixhawk-params (uit log 165 / paramdump):** `WPNAV_SPEED=100`
(1 m/s), `WPNAV_SPEED_UP/DN=50`, `RTL_ALT=500` (5 m), `GUID_TIMEOUT=3.0`,
`BATT_LOW_VOLT=14.0`, `BATT_CRT_VOLT=13.6`, `FS_GCS_ENABLE=0`.

---

## 5. Faalgevallen → RTL overal

Elke fase is begrensd in tijd/afstand en valt bij falen terug op **RTL** met een
statusmelding die de reden noemt. **Nooit onbepaald LOITER** — dat verbrandt de
harde accugrens. De zender-override blijft altijd primair (mode ≠ GUIDED =
onmiddellijk stoppen, zoals in mission.py/pattern.py/approach.py).

- **Hovertest afgekeurd** → LAND op het opstijgpunt, met de gemeten waarden
  in de melding. Niet RTL: bij een mechanisch probleem wil je niet eerst
  ergens heen vliegen.
- **Alle kandidaten verworpen** → RTL, meld welke bearings geprobeerd zijn.
- **RSSI stijgt nooit bij naderen** → je loopt niet naar een echte bron → stop, RTL.
- **Instort niet gevonden in de pass** → pass voltooit toch; log het beste-RSSI-punt
  als terugval met lage confidence, RTL.

---

## 6. Accu-budget

Hover ≈ 290 mAh/min → **harde grens ~5,0 min (300 s)**. Begroting van de
huidige opzet bij een beacon op ~18 m en één rake kandidaat:

| Post | Tijd |
|---|---|
| Takeoff + settle + arm | ~15 s |
| Fase 0 hovertest | ~5 s |
| Fase 1 peilen (12 × 30°, bij 33% pakketverlies) | ~93 s |
| Fase 2 richtdraai + 2 metingen | ~12 s |
| Fase 2 verificatiestap 10 m @ 1 m/s | ~10 s |
| Fase 3 draai + uitzweven | ~6 s |
| Fase 3 doorvliegen ~15 m @ 0,5 m/s | ~30 s |
| Fase 4 180° draai + uitzweven | ~12 s |
| Fase 4 terugkruising ~12 m @ 0,5 m/s | ~24 s |
| Fase 5 RTL + landen | ~30 s |
| Aankondigingen op de kaart (4 × 1,5 s) | ~6 s |
| **Totaal** | **~243 s = 4,1 min** |

Marge tot 5,0 min: **57 s**. Tot 4,5 min: 27 s. De hovertest kost 5 s en kan een hele vlucht besparen; de RTL op zoekhoogte wint een deel daarvan terug doordat er niet eerst geklommen wordt.

Werkelijke veldvluchten ter controle: 2-8 17:18 was 221 s toen de piloot
overnam en had nog ~85 s te gaan (zou over de grens zijn gegaan met de oude,
stapsgewijze nadering); 2-8 19:12 met de huidige opzet was **178 s in GUIDED
inclusief RTL** — ruim binnen de grens.

**Op > 25 m past het nog steeds niet.** Nadering en RTL schalen met de
afstand. v1 blijft ~10-25 m.

## 7. Structuur en hergebruik

**`search.py`**, naast `pattern.py` en `approach.py`.

Hergebruik:
- **`mission.py`** — `_cmd_set_mode_guided`, `_cmd_arm`, `_cmd_takeoff`,
  `_clamp_altitude` (veiligheidsmodel + basiscommando's, ongewijzigd).
- **`approach.py`** — `_wacht_op_stap` (fase 2), `_cmd_rtl`,
  `_horizontale_afstand`, en het thread/state/`finally`-patroon.
- **`pattern.py`** — `_wacht`, `_pilot_has_taken_over`, `_verzamel_metingen`
  (stilstaand N packets met mediaan; gebruikt door fase 1 én 2).

Eigen commando-helpers in search.py:
- `_cmd_positie_offset` — positie-setpoint in `LOCAL_OFFSET_NED` met vast
  yaw-veld. Bestond niet in mission.py. Relatief frame en niet GLOBAL_INT om
  dezelfde reden als `pattern._cmd_hoogtestap`: een teken- of eenheidsfout
  kost hooguit één stapgrootte, terwijl GLOBAL_INT de hoogte als meters
  boven ZEENIVEAU leest en de drone tot de grond zou laten dalen.
- `_cmd_yaw_kortste` — absolute yaw via de kortste weg. `pattern`'s versie
  heeft de richting hardgecodeerd op met-de-klok-mee; zie fase 1.
- `_cmd_set_param` — alleen om `WPNAV_SPEED` tijdelijk op 50 cm/s te zetten,
  met terugzetten in het `finally`-blok.

**Alleen fase 3 en 4 loggen continu** (`_log_continu`): dat zijn doorlopende
vluchten waarbij de instort achteraf uit de reeks komt en de GPS-positie
leidend is, niet de hoek. Fase 1 gebruikte dit ook en dat was fout — zie §3.
Gebruik die helper niet opnieuw voor een peiling.

Nieuwe telemetrie in app.py voor de hovertest: `VIBRATION` vult
`status['vibe_x/y/z']` en `status['vibe_clip']`, `SERVO_OUTPUT_RAW` vult
`status['motor_pwm']`. Beide komen mee met `MAV_DATA_STREAM_ALL` op 4 Hz;
er wordt niets extra aangevraagd. Geverifieerd dat ze live binnenkomen.

Dashboard: `START MISSIE` → socket-event `start_mission` → `search.start_search`.
Voortgang via `meting_update` naar `#mission-status`. De gevonden positie
gaat server-side naar de coördinaat-log (`log_beacon_positie` in app.py) en
verschijnt via `log_entry_added` live als pin op de kaart. De demo-missie
van mission.py zit achter `start_demo_mission`, alleen vanuit de console.

Elke zet wordt vóór uitvoering op de kaart aangekondigd (`search_richting`
→ `map.js`), met een pauze van `AANKONDIGING_S` zodat de operator kan
ingrijpen vóór de drone vertrekt in plaats van erna.

## 8. Wat nog open staat

### Het 45 dB link-tekort — de grootste onopgeloste post
Bij 2 dBm zendvermogen op 433 MHz zou de RSSI in vrije ruimte veel sterker
moeten zijn dan gemeten. Het tekort is **constant over de afstand**, en dat
betekent een vaste verliespost in de keten, geen propagatie-effect:

| meting | schuine afstand | verwacht | gemeten | tekort |
|---|---|---|---|---|
| nadering, 15 m | 15,3 m | −46,9 dBm | −93 | 46,1 dB |
| nadering, 6 m | 6,7 m | −39,7 dBm | −86 | 46,3 dB |
| zoekvlucht 21:05 | 18,6 m | −48,6 dBm | −93 | 44,4 dB |

Verdachten: kruispolarisatie (15-25 dB), een niet-resonante zend- of
ontvangstantenne (10-25 dB), kabel/connector. Twee daarvan samen dekken het.

Dit hangt samen met de brede lob: bij een sterk onderdrukte directe straal
ontvang je vooral gedepolariseerde verstrooiing, en verstrooiing heeft geen
richting. Een lob van 150-180° is precies wat je dan verwacht.

Golflengte op 433,2 MHz = 692 mm; kwartgolf 173 mm, Yagi gevoed element
~325 mm. **Polarisatie: de as van de beacon-cilinder moet evenwijdig lopen
aan de ELEMENTEN van de Yagi, niet aan de boom.** Verdraaiing kost
1,2 dB bij 30°, 3 dB bij 45°, 15-25 dB bij 90°. Een lineaire antenne straalt
bovendien niets uit langs zijn eigen as — leg de beacon-cilinder dus nooit
in de richting van de drone.

### Pakketverlies: 1,5-1,9 Hz in plaats van 2 Hz
Tijdens de kruisingen kwam een sample elke 0,52-0,68 m binnen in plaats van
de begrote 0,25 m. Dat halveert de resolutie precies waar de instort
gevonden moet worden.

De ontvangstlus pollt op 20 Hz en kan een 2 Hz-beacon niet missen, dus het
verlies zit op radioniveau of bij de zender. **De code telt CRC-fouten nu
niet**, dus we kunnen "wel ontvangen maar corrupt" niet onderscheiden van
"er kwam niets". Zonder die teller is elke verklaring een gok; hem toevoegen
is de eerste stap.

### Peilnauwkeurigheid
Het zwaartepunt haalde 1,8° / 5,7° / 11,5° op drie vluchten. Een Monte
Carlo op het gemeten patroon van 19:12 laat zien dat de meetruis (σ ≈ 0,7 dB
bij 5 packets) daar ~5° van verklaart; de rest zit in het patroon zelf.
Meer packets per hoek helpt dus maar tot ~2,7° en kost lineair vliegtijd.
De echte winst zit in een smallere lob — zie het link-tekort hierboven.

### Mechanische toestand van het toestel — het toestel vliegt niet meer
De val van 2-8 21:26 was mechanisch, geen software (zie fase 0). Sindsdien is
de hovertest **vier keer op rij afgekeurd** en is er niet meer gezocht.

Wat er is gevonden en gerepareerd:

| | gevonden | gerepareerd | effect op de meting |
|---|---|---|---|
| ESC M1 linksvoor (CW) | na de val van 2-8 | ja | de val zelf verklaard |
| ESC M4 linksachter (CCW) | 4-8, bij de tweede inspectie | ja | trilling terug naar 26; M4 weer gelijk met zijn CCW-partner |
| frame 5 mm uit het vierkant aan de rechterzijde | 4-8 | nee | geen; armlengte raakt de yaw-as niet |
| licht verschoven wikkelingen in een motor | 4-8 | nee | onbekend |

De reparatie van M4 is meetbaar geslaagd: de trilling zakte van 39,9 naar
26,0, de rol- en pitch-as kwamen terug op nul, en M4 werd binnen zijn eigen
draairichtingspaar weer gelijk aan M3 (van +104 PWM erboven naar −1). **Het
draaikoppel bewoog niet**: −346 vóór, −317 na. Het is dus een tweede,
onafhankelijk mankement.

Wat er níét de oorzaak van kan zijn, en waarom:

- **Zwaartepunt.** Kan geen koppel om de verticale as maken; de rol-as is bovendien onveranderd.
- **Propellers.** Het zijn dezelfde vier die log 170 met yaw +93 vlogen, nooit vervangen, en carbon breekt in plaats van te plooien.
- **Een enkele zwakke motor of ESC.** Motorsterkte schaalt stuwkracht en koppel samen; de verhouding koppel/stuwkracht is een eigenschap van de propeller, niet van de motor. Bovendien wordt niet één kanaal opgetild maar een heel diagonaal paar, en dat past niet bij één zwakke motor.
- **De verwijderde telemetriemodule.** Massa verplaatsen maakt rol- en pitchmomenten, geen draaikoppel.

Wat het wél kan zijn: een tangentiële scheefstand van enkele graden op een
motorsteun of een arm die in zijn klem is verdraaid — dat geeft koppel zonder
noemenswaardig stuwkrachtverlies en is met het oog niet te zien — of
inwendige wrijving in een motor, die koppel kost maar geen stuwkracht.

**Besluit: opnieuw bouwen.** Frame, motoren en snelheidsregelaars worden
vervangen; vluchtcontroller, GPS, Raspberry Pi en de **volledige radioketen**
(beacon, Yagi, ontvanger, kabels, montagepositie) verhuizen ongewijzigd mee.
Die laatste is niet onderhandelbaar: verandert daar iets, dan zijn de
peilnauwkeurigheid, de lobbreedte en het link-tekort uit §2 en §8 niet meer
vergelijkbaar met wat erna komt.

De hovertest wordt daarmee de **acceptatietest van de nieuwe bouw**: spreiding
onder 128 PWM, yaw-as tussen +73 en +94, klimdraai onder 10°, trilling onder
30. Haalt de nieuwe bouw dat, dan is het aantoonbaar hetzelfde toestel als dat
van de zes geslaagde meetvluchten en blijven alle eerdere metingen geldig.

Wat de hovertest niet uitsluit: een propeller met een haarscheur of een losse
motorschroef geven pas onder toerental problemen. Handmatig draaien sluit
alleen een vastgelopen lager uit. De eerste vlucht na een reparatie is dus
meteen de meting die zegt of het geholpen heeft — de waarden staan in de
CSV-kop.

Trillingen waren al vóór de val verhoogd: ~26 tegen 12-17 op eerdere
vluchten.

### Overig
- **Instort-scherpte bij een gladde pass** blijft de minst geverifieerde
  aanname. Op 19:12 gaf de heenweg 3,6 dB en de terugweg 1,7 dB bij een
  zijwaartse misser van ~3,7 m.
- **Hoogte**: de takeoff overschiet met 0,4-0,8 m en de `z=0`-setpoints
  verankeren dat. Gevraagd 2,5 m, gevlogen 2,9-3,3 m. De regeling zelf is
  goed (Alt−DAlt binnen ±0,2 m).
- **Peiling-contrast bij te korte afstand** (< ~10 m) is nooit gemeten.
- **De veldtracker zendt lager dan 2 Hz** voor batterijduur; peilen wordt
  dan trager.
- **Polarisatie in het veld is oncontroleerbaar**: een tracker op een levende
  hoornaar draait en kantelt. Een circulair gepolariseerde ontvangstantenne
  ruilt 0-25 dB wisselend verlies in voor een vaste 3 dB.

## 9. Wat de veldvluchten hebben veranderd

| Vlucht | Bevinding | Gevolg |
|---|---|---|
| 1-8 17:38 | continue draai koppelde metingen aan de verkeerde heading; kandidaten 76-84° naast de beacon | fase 1 stapsgewijs, 12 × 30° |
| 1-8 21:05 | zelfde peilfout met de piek; zwaartepunt zat er 5,7° naast | kandidaatkeuze naar het zwaartepunt |
| 1-8 (analyse) | 5 m verificatiestap kan voorbij 17 m nooit 3 dB halen | stap naar 10 m |
| 2-8 17:18 | drone stond op 2,70 m van de beacon en vloog er 21 m vandaan; yaw draaide tijdens de pass en gaf een valse instort van 6 dB met betrouwbaarheid HOOG, 20,9 m naast de beacon | gradiënt-nadering vervangen door doorvliegen; yaw uitdraaien vóór het loggen |
| 2-8 19:12 | eerste geslaagde volledige vlucht; twee kruisingen 8,0 m uit elkaar werden niet gemiddeld, terwijl het middelpunt beter was | altijd middelen, betrouwbaarheid uit de spreiding |
| alle drie | eerste peilstap draaide bijna 360° de verkeerde kant op en werd midden in de draai gemeten | `_cmd_yaw_kortste`; niet-bereikte hoek wordt overgeslagen |
| 2-8 21:26 | val na de richtingbepaling; mechanisch (trilling eerst, stand daarna), motoren stonden al 103 PWM scheef tijdens de hover | hovertest als fase 0; `VIBRATION` en `SERVO_OUTPUT_RAW` in de status-dict |
| 2-8 21:26 | peiling 5,8° naast de beacon bij een 170° gedraaide geometrie — beste resultaat tot dan, op een onafhankelijke opstelling | bevestigt zwaartepunt + kortste-weg-draai |
| 3-8 (3×) | hovertest keurt af op 361-413 PWM spreiding en 117-125° klimdraai; niet gevlogen | drempels blijken juist geijkt; toestel is het probleem, niet de test |
| 3-8 (analyse) | logkolommen `C1..C4` zijn niet motor 1-4; de scheefstand zit in de **yaw-as**, niet in de rol-as | §3 rechtgezet; zwaartepunt-hypothese vervalt |
| 4-8 | tweede bezweken ESC-soldering (M4 linksachter) hersteld: trilling en rol/pitch terug op de basislijn, **draaikoppel onveranderd** | tweede, onafhankelijk mankement; niet gelokaliseerd |
| 4-8 | yaw-integrator komt in evenwicht op zijn `IMAX`; twee motoren op 78% van hun bereik tegen 59% normaal | geen reserve meer voor wind of draaicommando's |
| 4-8 | vijf inspecties vinden de oorzaak van het draaikoppel niet; frame niet te openen | herbouw met behoud van de volledige radioketen; hovertest wordt acceptatietest |
