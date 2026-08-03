#!/usr/bin/env python3
"""
search.py — autonoom zoekalgoritme: lokaliseer de LoRa-beacon en log de positie.

Dit is de eerste OPERATIONELE functie van VespaTrack. pattern.py en approach.py
waren meetinstrumenten om te leren hoe het signaal zich gedraagt; deze module
gebruikt die kennis om het nest te vinden en de coördinaat vast te leggen voor
de verdelger. Het volledige ontwerp met de herkomst van elke parameter staat in
docs/zoekalgoritme-ontwerp.md — dat document is de bron, deze code volgt het.

DE VIJF FASEN

    1. peilen      12 stops van 30°: draai, stop, meet stilstaand, draai door
                   -> mediaan per hoek -> kandidaten
    2. verifiëren  5 m in de kandidaatrichting; RSSI moet >= 3 dB stijgen,
                   anders is het een reflectie en proberen we de volgende
    3. doorvliegen één doorlopende vlucht op 0,5 m/s met continue meting,
                   dwars over de bron heen; instort ACHTERAF uit de log
    4. terugkruisen 180° draaien en dezelfde lijn terug meten; het middelpunt
                   van de twee kruisingen heft een meetvertraging op
    5. afronden    RTL + coördinaat-log-entry + toast

WAAROM ER NIET MEER GECORRIGEERD WORDT ONDERWEG
Fase 3 naderde eerst in stappen van 5 m met een koerscorrectie op de
RSSI-gradiënt. Dat is verwijderd na de vlucht van 2-8: de drone stond op
2,70 m van de beacon met het sterkste signaal van de vlucht en vloog er
daarna 21 m vandaan, omdat de gradiëntlogica "ik loop ernaast" niet kon
onderscheiden van "ik ben er net overheen".

Het kan nu ook zonder. Het zwaartepunt peilt op ~4-6°, en bij 17 m passeer
je dan op 1,2-1,8 m van de bron — ruim binnen het bereik waarop de instort
zichtbaar is. Eén doorlopende vlucht op 0,5 m/s geeft bovendien een sample
elke 0,25 m, tegen één punt per 5 m bij stappen.

De YAW ligt tijdens elke vlucht vast en wordt ERVOOR uitgedraaid: de antenne
is richtingsgevoelig, dus een draaiende neus tijdens het meten levert een
signaaldaling op die op een instort lijkt. Dat is precies wat op 2-8 misging.
Daarvoor was het positie-setpoint met vast yaw-veld nodig dat mission.py
niet had.

WAAROM FASE 1 STILSTAAND MEET EN NIET TIJDENS EEN CONTINUE DRAAI
Fase 1 draaide eerder in één vloeiende beweging rond terwijl elk packet aan
de heading van dat moment werd geplakt. Dat is teruggedraaid, en het is de
belangrijkste les uit de eerste veldvlucht: meetwaarde en heading komen uit
verschillende bronnen met verschillende ververssnelheden (LoRa-RX-thread,
signal_loop op 10 Hz, MAVLink op 4 Hz), dus terwijl de drone draait hoort
een rij niet gegarandeerd bij één hoek. Stilstaand meten maakt die hele
vraag irrelevant — beweegt de drone niet, dan is de hoek gewoon de hoek.
Het is bovendien de methode van de zes GESLAAGDE rotatiemetingen in
pattern.py; de continue variant was een accu-optimalisatie en is de enige
peiling die faalde.

Fase 1, 2 en 3 hergebruiken daarom pattern._verzamel_metingen (stilstaand,
N packets, mediaan). Alleen fase 4 meet nog tijdens beweging — daar MOET
dat, want de pass is één doorlopende vlucht en de instort wordt achteraf
uit de reeks gehaald. Zie _log_continu.

VEILIGHEIDSMODEL (identiek aan mission.py / pattern.py / approach.py)
  - De zender heeft ALTIJD voorrang. Zodra flight_mode niet meer GUIDED is,
    stoppen we onmiddellijk met commando's sturen, schrijven weg wat we
    hebben, en eindigen.
  - Er wordt NOOIT gedisarmd in de lucht.
  - Elke faalweg eindigt in RTL met een melding die de reden noemt — nooit
    onbepaald LOITER. De accu is de harde grens: ~5 min per lading.
  - Bij een exception wordt de tot dan verzamelde data alsnog weggeschreven.

v1 IS BEWUST KORTE AFSTAND (~10-25 m) EN ÉÉN VLUCHT. Verder weg past niet op
één accu; dichterbij verzadigt de SNR in alle richtingen en geeft fase 1 geen
hoekcontrast. Zie ontwerpdoc §1 en §8.
"""

import csv
import math
import os
import statistics
import threading
import time
from datetime import datetime

import approach
import mission
import pattern


# ============================================
# HOVERTEST — toestandscontrole vóór het zoeken
# ============================================
#
# Waarom deze test bestaat: op 2-8 21:26 viel de drone recht naar beneden na
# de richtingbepaling. Uit het .BIN bleek een mechanische oorzaak — de
# trilling schoot van 26 naar 68 in vier tienden van een seconde, en pas
# DAARNA liep de stand weg. De EKF- en kompasmeldingen in de log kwamen 1,5
# tot 3 s later en waren gevolg, geen oorzaak.
#
# Wat er al vóór de val zichtbaar was: de vier motoren stonden 103 PWM uit
# elkaar tijdens rustig stilhangen (1623 / 1708 / 1725 / 1626). Een quad in
# balans zit binnen enkele tientallen. Die scheefstand stond in de log van
# minuut één — alleen keek er niemand naar vóór de vlucht.
#
# Deze test hangt dus even stil op zoekhoogte, leest wat de vluchtcontroller
# zelf al meet, en gaat alleen door als het klopt. Kosten: een paar seconden
# die je toch al aan uitzweven kwijt bent.
#
# DE DREMPELS ZIJN VOORLOPIG. De .BIN-logs van de gezonde vluchten zijn niet
# meer beschikbaar, dus ze staan nu ruim: ze vangen een duidelijk defect,
# niet een beginnende scheefstand. Elke vlucht schrijft de gemeten waarden in
# de CSV-kop; met een paar gezonde vluchten erbij kunnen ze strakker.

HOVERTEST_DUUR_S = 5.0        # meetvenster; lang genoeg voor ~20 monsters op 4 Hz

# ArduPilot-richtlijn: < 30 goed, 30-60 twijfelachtig, > 60 probleem.
# Bij de val stond hij op 26 vlak vóór het defect en piekte op 114.
HOVERTEST_VIBE_WAARSCHUWING = 30.0
HOVERTEST_VIBE_MAX          = 60.0

# Verschil tussen de hardst en zachtst draaiende motor bij stilhangen.
# Bij de val: 103 PWM. Een toestel in balans haalt dat bij lange na niet.
HOVERTEST_MOTOR_WAARSCHUWING = 80
HOVERTEST_MOTOR_MAX          = 150

# Clipping betekent dat de versnellingsmeter zijn bereik raakt: dan is de
# hoogte- en standschatting niet meer te vertrouwen. Nul is de enige
# acceptabele waarde bij stilhangen.
HOVERTEST_CLIP_MAX = 0


def _hovertest(status, duur_s=None):
    """
    Hang stil en lees de toestandsbewaking van de vluchtcontroller uit.

    Meet trilling (VIBRATION) en de spreiding tussen de vier motoren
    (SERVO_OUTPUT_RAW) over een venster. Beide komen uit de Pixhawk zelf; we
    voegen niets toe, we kijken alleen naar wat er toch al gemeten wordt.

    Returns (in_orde, oordeel_tekst, metingen_dict). Ontbreekt de telemetrie
    (oudere firmware, stream niet actief), dan is in_orde True met een
    melding dat er niet gemeten kon worden — een ontbrekende sensor mag geen
    vlucht blokkeren, maar moet wel opvallen in de log.
    """
    duur = HOVERTEST_DUUR_S if duur_s is None else duur_s
    vibes, spreidingen = [], []
    clip_begin = status.get('vibe_clip', 0)

    einde = time.time() + duur
    while time.time() < einde:
        if pattern._pilot_has_taken_over(status):
            return False, 'piloot nam over tijdens de hovertest', {}
        vx = status.get('vibe_x', -1.0)
        if vx >= 0:
            vibes.append(max(vx, status.get('vibe_y', 0.0),
                             status.get('vibe_z', 0.0)))
        pwm = status.get('motor_pwm') or []
        if len(pwm) >= 4 and min(pwm[:4]) > 0:
            spreidingen.append(max(pwm[:4]) - min(pwm[:4]))
        time.sleep(0.1)

    clip = status.get('vibe_clip', 0) - clip_begin
    meting = {
        'vibe_max': round(max(vibes), 1) if vibes else None,
        'vibe_gem': round(statistics.mean(vibes), 1) if vibes else None,
        'motor_spreiding': round(statistics.median(spreidingen)) if spreidingen else None,
        'clip': clip,
        'n': len(vibes),
    }

    if not vibes and not spreidingen:
        return True, ('geen toestandstelemetrie ontvangen — hovertest '
                      'overgeslagen'), meting

    redenen = []
    if meting['vibe_max'] is not None and meting['vibe_max'] > HOVERTEST_VIBE_MAX:
        redenen.append(f"trilling {meting['vibe_max']:.0f} > {HOVERTEST_VIBE_MAX:.0f}")
    if (meting['motor_spreiding'] is not None
            and meting['motor_spreiding'] > HOVERTEST_MOTOR_MAX):
        redenen.append(f"motoren {meting['motor_spreiding']} PWM uit elkaar "
                       f"> {HOVERTEST_MOTOR_MAX}")
    if clip > HOVERTEST_CLIP_MAX:
        redenen.append(f'{clip} keer clipping op de versnellingsmeter')

    tekst = (f"trilling max {meting['vibe_max']}, gem {meting['vibe_gem']}; "
             f"motoren {meting['motor_spreiding']} PWM uit elkaar; "
             f"clipping {clip}")
    if redenen:
        return False, tekst + ' -> AFGEKEURD: ' + ', '.join(redenen), meting

    let_op = []
    if meting['vibe_max'] is not None and meting['vibe_max'] > HOVERTEST_VIBE_WAARSCHUWING:
        let_op.append('trilling verhoogd')
    if (meting['motor_spreiding'] is not None
            and meting['motor_spreiding'] > HOVERTEST_MOTOR_WAARSCHUWING):
        let_op.append('motoren staan scheef')
    if let_op:
        tekst += ' -> let op: ' + ', '.join(let_op)
    return True, tekst, meting


# ============================================
# FASE 1 — PEILEN
# ============================================

# STAPSGEWIJS METEN — NIET CONTINU DRAAIEN.
#
# Deze fase draaide eerder in één vloeiende beweging rond terwijl elk
# binnenkomend packet aan de heading van dát moment werd geplakt. Dat is
# teruggedraaid. Lees dit voordat je het "optimaliseert":
#
#   Bij een continue draai komt de meetwaarde uit een andere bron en op een
#   ander moment dan de heading. De LoRa-ontvangstthread schrijft SNR en de
#   packetteller meteen weg, de RSSI wordt pas door signal_loop (10 Hz)
#   bijgewerkt, en de heading komt via GLOBAL_POSITION_INT op 4 Hz binnen.
#   Elke waarde in een rij kan dus van een ander tijdstip komen. Zolang de
#   drone draait, betekent "een ander tijdstip" ook "een andere hoek".
#
#   Stilstaand meten haalt die afhankelijkheid volledig weg: als de drone
#   niet beweegt, maakt het niet uit of de heading 250 ms oud is. De hoek
#   is dan gewoon de hoek. Geen kalibratie, geen aanname over vertragingen,
#   geen bewijslast — de fout kan simpelweg niet ontstaan.
#
# Dit is bovendien de methode van de zes GESLAAGDE rotatiemetingen
# (pattern.py): draai naar de hoek, stop, laat uitzweven, meet. De continue
# variant was een accu-optimalisatie en is de enige peiling die faalde.
#
# 30° in plaats van pattern.py's 10°: 12 stops in plaats van 36, een derde
# van de meettijd. Dat past op de accu en is nog steeds fijn genoeg t.o.v.
# de ±30° peilnauwkeurigheid die de metingen lieten zien.
PEIL_STAP_GRADEN     = 30
PEIL_AANTAL_STAPPEN  = 12          # 12 x 30° = volledige cirkel
PEIL_METINGEN_PER_HOEK = pattern.METINGEN_PER_HOEK   # 5 packets per hoek

# Uitzweven ná de draai en vóór de meting. De antenne moet stil hangen; een
# nog uitzwaaiende drone meet zijn eigen beweging mee.
SETTLE_NA_PEILSTAP_S = 1.5

# Met de kortste draairichting (_cmd_yaw_kortste) is 180° de grootst
# mogelijke draai; bij pattern.YAW_RATE_DPS (20°/s) is dat 9 s. 15 s laat
# marge voor wind en voor het aanzetten en afremmen.
PEIL_HEADING_TIMEOUT_S = 15.0

# Onder dit aantal hoeken mét packets is de peiling niet te vertrouwen: dan
# heeft de beacon te vaak gezwegen om een richting uit te halen. Doorvliegen
# op zo'n peiling is erger dan naar huis gaan.
MIN_HOEKEN_MET_SIGNAAL = 8

# Harde bovengrens op fase 1. Valt de beacon stil, dan loopt elke meting in
# zijn packet-timeout en kan deze fase de hele accu opeten voordat er ook
# maar één stap gevlogen is.
PEIL_MAX_DUUR_S = 150.0

# RICHTINGSCHATTING: ZWAARTEPUNT, NIET DE STERKSTE HOEK.
#
# De sterkste hoek nemen faalde op twee opeenvolgende veldvluchten, allebei
# met een fout van 76-84°. De reden is gemeten: de hoofdlob is ~150° breed en
# de SNR varieert daarbinnen maar ~1,0 dB, terwijl de ruis op het verschil
# tussen twee hoeken (mediaan van 5 packets, sigma ~1,3 dB per packet) óók
# ~1,0 dB is. Welke hoek binnen de lob toevallig de hoogste waarde krijgt is
# dus een muntworp.
#
# Het zwaartepunt van dezelfde meetreeks wijst wél naar de bron — op beide
# vluchten, met de beacon-richting onafhankelijk uit GPS-coördinaten bepaald:
#
#            beacon      piek        zwaartepunt
#   1-8 21:05  132,6°   208,9° (76° eraf)   126,9° (5,7° eraf)
#   1-8 17:38  133,7°   217,5° (84° eraf)   131,9° (1,8° eraf)
#
# BEKENDE ZWAKTE: het zwaartepunt is gevoelig voor een tweede bron. Getest op
# een synthetisch patroon met een reflectie op 120° van de hoofdlob:
#   reflectie 4 dB zwakker -> 0° fout;  2 dB zwakker -> 13°;  even sterk -> 60°.
# Het ontwerp gaat uit van een boomrand-reflectie ~2 dB onder de beacon, dus
# dit is precies de zone waar het begint te schuiven. Daarom blijft de piek
# als TWEEDE kandidaat bestaan wanneer hij ver van het zwaartepunt ligt: dan
# zijn het twee echt verschillende hypotheses en beslist fase 2 ertussen.
#
# Weging: 10^((snr - snr_min)/10), dus lineair vermogen ten opzichte van de
# zwakste gemeten richting. SNR en niet RSSI omdat SNR op beide vluchten
# dichter uitkwam (5,7/1,8° tegen 6,8/7,4°).
#
# Aanname: de meetpunten liggen ongeveer gelijk over de cirkel verdeeld. Bij
# 12 stappen van 30° klopt dat; een vectorsom weegt niet naar hoekafstand,
# dus bij sterk ongelijke verdeling zou dit scheeftrekken.
PIEK_AFWIJKING_GRADEN = 45.0

# Hoeveel kandidaten we maximaal proberen. Elke verworpen kandidaat kost
# ~50 s (verificatiestap heen en terug op 10 m) en het accubudget draagt er
# één (ontwerp §6).
MAX_KANDIDATEN = 2


# ============================================
# FASE 2 — VERIFIËREN
# ============================================

# Lengte van de verificatiestap. WAS 5 m, en dat kon rekenkundig niet werken.
#
# Padverlies loopt met 20*log10(d2/d1), dus de winst van een stap hangt af van
# de AFSTAND, niet van een vaste dB/m. De oude onderbouwing ("RSSI stijgt
# ~0,8 dB/m, dus 5 m geeft ~4 dB") nam een helling die alleen rond 11 m geldt
# en behandelde die als constante. Wat een stap werkelijk oplevert bij een
# perfect gepeilde richting:
#
#     afstand    stap 5 m   stap 10 m
#       15 m      3,52 dB     9,54 dB
#       18 m      2,83 dB     7,04 dB   <- beacon lag hier op de vlucht van 1-8
#       21 m      2,36 dB     5,62 dB
#       25 m      1,94 dB     4,44 dB
#
# Met de drempel op 3 dB betekende 5 m dat vanaf ~17 m GEEN ENKELE kandidaat
# kon slagen, ook de juiste niet. Precies dat gebeurde twee vluchten op rij.
#
# 10 m verdraagt bovendien een slordige peiling. Maximale peilfout die nog
# +3 dB haalt op 18 m: met 5 m geen enkele, met 10 m tot +-43°. Dat dekt de
# ~6° van het zwaartepunt met ruime marge.
#
# LET OP bij korte afstand: onder ~10 m schiet deze stap over de bron heen.
# Dat is niet schadelijk (de RSSI stijgt dan juist fors, dus de kandidaat
# wordt bevestigd) en fase 3 corrigeert het met zijn heuvelklim. Verificatie
# wordt sowieso overgeslagen boven VERIFY_OVERSLAAN_RSSI.
VERIFICATIE_STAP_M = 10.0
VERIFY_STIJGING_DB = 3.0   # onder de verwachte 4 dB, boven de ruis (2,5 dB)

# Boven deze RSSI slaan we de verificatie over: de drone staat dan al zo
# dichtbij dat de RSSI verzadigd is en over 5 m geen 3 dB meer kan stijgen —
# verify zou de echte bron onterecht verwerpen. Uit de naderingsmeting
# (log 165): -93 dBm op 15 m, -86 dBm op 6 m. -88 dBm ligt op ~7-8 m, net
# vóór de verzadiging.
#
# Dit is een GROVE afstandsschatting en wordt daarom alleen gebruikt om een
# verificatie over te slaan (een fout kost hooguit een overbodige of gemiste
# controlestap). Als stopcriterium tijdens het doorvliegen zou een absolute
# RSSI niet deugen: de hoek levert 9-11 dB, meer dan het hele afstandsbereik.
# Daarom stopt fase 3 op een DALING t.o.v. het eigen sterkste punt.
VERIFY_OVERSLAAN_RSSI = -88.0


# ============================================
# FASE 3 EN 4 — DOORVLIEGEN EN TERUGKRUISEN
# ============================================
#
# EEN DOORLOPENDE VLUCHT, GEEN STAPPEN MEER.
#
# Fase 3 naderde eerder in stappen van 5 m en corrigeerde na elke stap de
# koers op de RSSI-gradiënt. Dat is verwijderd na de vlucht van 2-8. Wat er
# gebeurde: de drone stond na twee stappen op 2,70 m van de beacon met het
# sterkste signaal van de vlucht (-81 dBm) en vloog er vervolgens 21 meter
# vandaan. De gradiëntlogica onthield alleen de beste WAARDE, niet de beste
# PLEK, en kon "ik loop ernaast" niet onderscheiden van "ik ben er net
# overheen" — beide geven een dalende RSSI. Drie koerscorrecties later was
# het stappenmaximum op en begon de pass vanaf het slechtste punt.
#
# Waarom doorvliegen beter is nu de peiling klopt:
#   - het zwaartepunt peilt op ~4-6°, en bij 17 m passeer je dan op 1,2-1,8 m
#     van de bron: ruim binnen het bereik waarop de instort zichtbaar is
#   - continu meten op 0,5 m/s bij 2 Hz geeft een sample elke 0,25 m, tegen
#     één punt per 5 m bij stappen — twintig keer fijner
#   - de instort is een veel sterker signaal (>5 dB over 1-3 m) dan de
#     gradiënt tussen twee stappen (~1,5 dB, gelijk aan de ruis)
#   - het is sneller: ~25 s in plaats van ~70 s voor stappen plus pass
#
# Wat we ervoor inleveren: er is geen koerscorrectie onderweg meer. Dat is
# een bewuste ruil — de correctie werkte aantoonbaar averechts, en de
# terugkruising hieronder geeft een tweede kans.

# Hoe ver we maximaal doorvliegen. De verificatiestap heeft al 10 m
# afgelegd, dus wat er rest is (oorspronkelijke afstand - 10 m) plus de
# overshoot. Bij een startafstand van 25 m is dat 15 + 5 = 20 m.
DOORVLIEG_MAX_M = 20.0

# Hoever we doorvliegen NA de instort. Genoeg om de daling volledig in de
# log te krijgen, niet meer.
DOORVLIEG_VOORBIJ_M = 5.0

# Live stopdrempel: is de RSSI zo ver onder het sterkste punt van deze
# vlucht gezakt, dan zijn we er voorbij. Dit stopt alleen de VLUCHT; de
# precieze positie komt nog altijd achteraf uit de log (zie _zoek_instort).
# Ruimer dan de instort-drempel zodat ruis hem niet vroegtijdig afbreekt.
INSTORT_LIVE_DB = 6.0

# DE TERUGKRUISING.
#
# Na het doorvliegen draait de drone 180° en vliegt dezelfde lijn terug,
# opnieuw metend. Dat levert een TWEEDE instort op, uit de tegengestelde
# richting.
#
# Waarom dat meer is dan een herkansing: elke systematische vertraging in
# de meetketen (de RSSI loopt een packet achter, plus verwerkingstijd)
# schuift de geschatte instortpositie een vast aantal meters VOORUIT langs
# de vliegrichting. Draai je om, dan schuift die fout precies de andere
# kant op. Het MIDDELPUNT van de twee kruisingen is er dus vrij van,
# zonder dat we de vertraging hoeven te kennen of te meten. Met één
# kruising kan dat principieel niet.
#
# De terugweg is bovendien grotendeels gratis: de drone moet toch naar huis.
TERUGKRUISING = True

# Hoe lang de neus mag uitdraaien vóór er gemeten wordt.
#
# Dit is geen cosmetische pauze. Op de vlucht van 2-8 begon de pass terwijl
# de yaw nog van 103° naar 143° draaide. De eerste vijf samples gaven SNR
# +6,0 -> +1,2 -> 0,0: dat was de antenne die wegdraaide, geen instort. Op
# die reeks zou _zoek_instort een positie hebben gelogd met betrouwbaarheid
# HOOG die 20,9 m naast de echte beacon lag. Een fout-positief met hoge
# zekerheid is het gevaarlijkste dat dit systeem kan produceren.
YAW_SETTLE_VOOR_PASS_S = 3.0

# Tijdens het doorvliegen en terugkruisen zetten we WPNAV_SPEED tijdelijk op
# 50 cm/s: bij 2 Hz geeft dat een sample elke 25 cm, fijn genoeg om een
# instort van ~1 m te zien. Daarna terug op de waarde uit de paramdump
# (100 = 1 m/s); blijft hij op 50 staan, dan wordt ook de RTL half zo snel
# en dat kost accu.
PASS_SNELHEID_CMS      = 50.0
STANDAARD_SNELHEID_CMS = 100.0

# RTL_ALT uit de paramdump (500 cm = 5 m). Tijdens fase 5 zetten we hem
# tijdelijk op de zoekhoogte zodat de drone niet eerst klimt.
STANDAARD_RTL_ALT_CM = 500.0

# Instort-detectie. De doc noemt ">5 dB daling over <2 m", maar in de
# naderingsmeting (log 165) haalt geen enkel 2 m-venster dat: 15->17 m is
# 1,6 dB, het steilste 2 m-venster 4,3 dB, en pas over 3 m kom je op 5,7 dB.
# Met 5 dB/2 m zou de detectie dus nooit vuren op de enige data die we
# hebben. 4 dB over 3 m vuurt daar wél op en blijft ruim boven de ruis.
# Die meting was schokkerig (stap-voor-stap); hoe een GLADDE pass eruitziet
# is nog onbekend — zie ontwerp §8, dit is de minst geverifieerde parameter.
INSTORT_VENSTER_M = 3.0
INSTORT_DALING_DB = 4.0

# Een zwakkere daling is nog steeds informatie: hij betekent dat we er
# lateraal naast zaten, niet dat we niets gepasseerd zijn. Het steilste punt
# ligt dan nog altijd dichter bij de beacon dan het sterkste-RSSI-punt (in de
# simulatie 3,3 m tegen 5,3 m). Daarom melden we dat als 'midden' in plaats
# van terug te vallen op de ruwste schatting.
INSTORT_ZWAK_DB = 2.0


# ============================================
# ALGEMEEN
# ============================================

METINGEN_PER_PUNT = pattern.METINGEN_PER_HOEK   # stilstaand meetpunt (fase 2/3)

SETTLE_NA_KLIM_S  = 3.0
SETTLE_NA_STAP_S  = 2.0
SETTLE_NA_DRAAI_S = 2.0
TAKEOFF_TIMEOUT_S = 30.0

# Ruim boven de 9 s die een halve draai kost bij pattern.YAW_RATE_DPS (20°/s):
# meer dan 180° hoeft een kandidaatrichting nooit te zijn.
DRAAI_TIMEOUT_S = 15.0

# Hoe lang de volgende zet op de kaart staat vóórdat de drone hem uitvoert.
#
# Zonder deze pauze verschijnt de lijn op hetzelfde moment dat het commando
# uitgaat, en dan leidt de operator de richting nog steeds af uit de
# beweging. Met een korte voorsprong ziet hij waar het toestel heen gaat
# terwijl het nog stilhangt, en kan hij ingrijpen vóór het vertrekt in
# plaats van erna.
#
# Kosten: ~1,5 s per beslissing. Een vlucht neemt er grofweg acht
# (kandidaten, naderingsstappen, de pass), dus ~12 s van de ~76 s accumarge
# bij 4°/s. Op 0.0 zetten houdt de kaartlijn maar haalt de wachttijd weg.
AANKONDIGING_S = 1.5

# Pre-flight eisen — gelijk aan de andere modules
MIN_SATELLITES   = 8
REQUIRE_3D_FIX   = True
BEACON_MAX_AGE_S = pattern.BEACON_MAX_AGE_S

# Onder deze accustand breken we af naar RTL in plaats van aan een volgende
# fase te beginnen. Ruimer dan pattern.py's rondecheck omdat een zoekvlucht
# altijd nog terug moet en de RTL zelf ~40 s kost.
MIN_BATTERIJ_PROCENT = 30

BEACON_TX_DBM = 2
LORA_CONFIG   = 'SF7 / BW125 kHz / 433.2 MHz'

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


# ============================================
# ZOEK-STATE (thread-safe gedeeld met dashboard)
# ============================================
# Eigen state, los van pattern/approach/mission. De voortgang gaat via het
# bestaande meting_update-kanaal naar #mission-status: één meldingsplek voor
# de operator, en die plumbing staat er al.

search_state = {
    'active': False,
    'step': 'idle',
    'message': '',
    'aborted_by_pilot': False,
    'hoogte': None,
    'fase': 0,
    'totaal_fasen': 5,
}
_lock = threading.Lock()
_thread = None


def _set_state(step, message, active=True, **extra):
    """Update de gedeelde zoek-state. Thread-safe."""
    with _lock:
        search_state['step'] = step
        search_state['message'] = message
        search_state['active'] = active
        search_state.update(extra)
    print(f"[search] {step}: {message}")


def get_search_state():
    """Lever een kopie van de zoek-state (voor het dashboard)."""
    with _lock:
        return dict(search_state)


# ============================================
# GEOMETRIE
# ============================================

def _hoekverschil(a, b):
    """Kleinste verschil tussen twee kompasrichtingen (0..180)."""
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d


def _noord_oost(bearing_deg, afstand_m):
    """
    Zet een kompasrichting + afstand om in een (noord, oost)-verplaatsing.

    Kompasrichting telt met de klok mee vanaf noord, dus noord = cos en
    oost = sin — omgekeerd aan de gebruikelijke wiskundige conventie.
    """
    r = math.radians(bearing_deg)
    return afstand_m * math.cos(r), afstand_m * math.sin(r)


# ============================================
# MAVLINK-COMMANDO'S
# ============================================
# Zelfde fire-and-forget aanpak als de andere modules: commando eruit,
# resultaat aflezen uit de status-dict die mavlink_loop toch al vult.

def _cmd_positie_offset(get_mav, mavutil, noord_m, oost_m, yaw_deg):
    """
    Vlieg een offset in NOORD/OOST-meters t.o.v. de HUIDIGE positie, met een
    opgelegde vaste yaw. Bestond nog niet in mission.py.

    Frame LOCAL_OFFSET_NED en niet GLOBAL_INT, om dezelfde reden als
    pattern._cmd_hoogtestap: bij GLOBAL_INT telt de hoogte in meters boven
    ZEENIVEAU en ligt ons meetveld op ~50 m MSL — een fout daarin laat de
    drone dalen tot hij de grond raakt. Relatief kan dat niet: een teken- of
    eenheidsfout kost hooguit één stapgrootte. Het frame is bovendien
    onafhankelijk van de heading, wat precies de 33° body-frame drift
    wegneemt die de naderingsmeting scheeftrok.

    z = 0 betekent "houd de huidige hoogte".

    Het yaw-veld is de reden dat deze helper bestaat: WP_YAW_BEHAVIOR staat
    op 2, dus zonder expliciete yaw draait de drone zijn neus naar het doel
    en verandert de antenne-oriëntatie tussen twee metingen.
    """
    mav = get_mav()
    if mav is None:
        return False

    # type_mask: positie + yaw gebruiken; snelheid, versnelling en yaw_rate
    # negeren. Dit is de mask van mission._cmd_forward met de yaw-negeerbit
    # (bit 10) uitgezet.
    type_mask = 0b0000101111111000
    mav.mav.set_position_target_local_ned_send(
        0,
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_FRAME_LOCAL_OFFSET_NED,
        type_mask,
        noord_m, oost_m, 0.0,
        0, 0, 0,
        0, 0, 0,
        math.radians(yaw_deg % 360), 0)
    return True


def _cmd_set_param(get_mav, mavutil, naam, waarde):
    """
    Zet één Pixhawk-parameter. Alleen gebruikt om WPNAV_SPEED tijdelijk te
    verlagen voor de overvlieg-pass.

    We lezen de waarde bewust NIET terug ter verificatie: de PARAM_VALUE die
    daarop volgt wordt door mavlink_loop() uit de stroom gehaald, en een
    tweede lezer op dezelfde connectie geeft een race. De terugzet-actie
    staat daarom in het finally-blok en gebruikt een vaste constante.
    """
    mav = get_mav()
    if mav is None:
        return False
    mav.mav.param_set_send(
        mav.target_system, mav.target_component,
        naam.encode('utf-8'), float(waarde),
        mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
    return True


# ============================================
# METEN
# ============================================

def _draai_naar(status, get_mav, mavutil, doel_graden):
    """
    Draai de neus naar een absolute kompasrichting en wacht tot hij er staat.

    Waarom dit een aparte stap is en niet meelift op het yaw-veld van het
    positie-setpoint: fase 2 vergelijkt de RSSI vóór en ná een stap van 5 m.
    Draait de neus tussen die twee metingen, dan zit er tot 9 dB antenne-
    effect in het verschil — ruim meer dan de 3 dB die de test moet aantonen,
    en elke kandidaat zou "bevestigd" worden, ook een reflectie. Eerst
    draaien, laten uitzweven, en dan pas de nulmeting.

    Gebruikt _cmd_yaw_kortste: absoluut (geen opstapelende fout over
    meerdere kandidaten) en via de kortste weg, zodat een draai nooit bijna
    een volle omwenteling wordt en in zijn timeout loopt.

    Returns (bereikt, afgebroken_door_piloot). Een niet-gehaalde hoek is geen
    reden om te stoppen — we meten dan op de hoek waar hij staat, en die
    staat in de log.
    """
    _cmd_yaw_kortste(get_mav, mavutil, doel_graden,
                     status.get('heading', 0.0))
    einde = time.time() + DRAAI_TIMEOUT_S
    while time.time() < einde:
        if pattern._pilot_has_taken_over(status):
            return False, True
        if _hoekverschil(status.get('heading', 0.0),
                         doel_graden) <= pattern.HEADING_TOLERANTIE:
            return True, False
        time.sleep(0.1)
    return False, False


def _cmd_yaw_kortste(get_mav, mavutil, doel_graden, huidige_graden):
    """
    Draai naar een absolute kompasrichting via de KORTSTE weg.

    pattern._cmd_yaw_absoluut heeft de richting hardgecodeerd op "met de klok
    mee". Voor pattern.py is dat onschadelijk — die loopt in stappen van +10°
    altijd vooruit. Voor de peiling hier is het dat niet: de eerste rasterhoek
    ligt willekeurig t.o.v. de stand na het opstijgen, en dan kan "met de klok
    mee" een draai van bijna 360° betekenen.

    Wat dat aanrichtte, op alle drie de veldvluchten hetzelfde: de eerste
    stap haalde zijn hoek niet binnen de timeout en werd MIDDEN IN DE DRAAI
    gemeten (commando 0°, gemeten op 71°, 92° en 75°). Tijdens die vijf
    packets draait de drone 60° door, dus die meetwaarde hoort bij geen
    enkele hoek in het bijzonder — precies het probleem dat stapsgewijs
    meten moest wegnemen.

    We rekenen de richting zelf uit en geven hem expliciet mee (+1 of -1) in
    plaats van 0 ("kortste") aan de firmware over te laten: dan hangt het
    gedrag niet af van hoe deze ArduCopter-versie param3 interpreteert.
    """
    mav = get_mav()
    if mav is None:
        return False
    verschil = ((doel_graden - huidige_graden + 180) % 360) - 180
    richting = 1 if verschil >= 0 else -1
    mav.mav.command_long_send(
        mav.target_system, mav.target_component,
        mavutil.mavlink.MAV_CMD_CONDITION_YAW, 0,
        float(doel_graden % 360),
        float(pattern.YAW_RATE_DPS),
        richting,
        0,                 # 0 = absolute hoek
        0, 0, 0)
    return True


def _log_continu(status, duur_s, fase, samples, klaar_fn=None):
    """
    Log elk binnenkomend LoRa-packet met de heading en GPS van dat moment,
    terwijl de drone beweegt.

    ALLEEN VOOR FASE 4. Fase 1 gebruikte dit ook en dat was fout: tijdens een
    draai hoort de gelogde hoek niet gegarandeerd bij de gelogde meetwaarde,
    omdat beide uit bronnen met verschillende ververssnelheden komen. Fase 1
    meet daarom stilstaand (zie _peil_stapsgewijs). Gebruik deze functie niet
    opnieuw voor een peiling.

    Voor de pass in fase 4 is er geen alternatief: die moet in één rechte,
    ononderbroken vlucht en de instort komt achteraf uit de reeks. Daar is de
    GPS-positie leidend en niet de hoek, dus de koppeling is minder gevoelig.

    We pollen op 20 Hz op lora_packet_count. De koppeling packet -> heading
    is daarmee hooguit 50 ms scheef; bij 4°/s is dat 0,2° en dus ruim binnen
    de 15°-vensters (zie ontwerp §8 — de skew is klein, maar pas bij een
    veldtest hard te bevestigen).

    klaar_fn is een optionele stopvoorwaarde (fase 4 stopt zodra de drone
    het pass-doel bereikt heeft in plaats van de volle duur uit te zitten).

    Returns (afgebroken_door_piloot, aantal_nieuwe_samples).
    """
    einde = time.time() + duur_s
    begin_aantal = len(samples)
    vorige_teller = status.get('lora_packet_count', 0)

    while time.time() < einde:
        if pattern._pilot_has_taken_over(status):
            return True, len(samples) - begin_aantal

        teller = status.get('lora_packet_count', 0)
        if teller > vorige_teller:
            vorige_teller = teller
            samples.append({
                'fase': fase,
                't': time.time(),
                'tijdstip': datetime.now().strftime('%H:%M:%S'),
                'lat': status.get('gps_lat', 0.0),
                'lon': status.get('gps_lon', 0.0),
                'alt': status.get('altitude', 0.0),
                'heading': status.get('heading', 0.0),
                'rssi': status.get('signal_power', -120.0),
                'snr': status.get('lora_snr', 0.0),
            })

        if klaar_fn is not None and klaar_fn():
            break

        time.sleep(0.05)

    return False, len(samples) - begin_aantal


def _meet_punt(status, fase, samples, opmerking=''):
    """
    Meet stilstaand op de huidige positie en voeg één rij toe aan samples.

    Hergebruikt pattern._verzamel_metingen: dat is exact een stilstaande
    meting van METINGEN_PER_PUNT packets, met dezelfde wacht-op-nieuw-packet
    logica. Mediaan en niet gemiddelde, om dezelfde reden als daar: losse
    willekeurige pieken mogen het punt niet meeslepen.

    Returns (rij_of_None, afgebroken_door_piloot). None als er geen enkel
    packet binnenkwam.
    """
    rssi_w, snr_w, afgebroken = pattern._verzamel_metingen(status)
    if not rssi_w:
        return None, afgebroken

    rij = {
        'fase': fase,
        't': time.time(),
        'tijdstip': datetime.now().strftime('%H:%M:%S'),
        'lat': status.get('gps_lat', 0.0),
        'lon': status.get('gps_lon', 0.0),
        'alt': status.get('altitude', 0.0),
        'heading': status.get('heading', 0.0),
        'rssi': round(statistics.median(rssi_w), 2),
        'snr': round(statistics.median(snr_w), 2),
        'n': len(rssi_w),
        'opmerking': opmerking,
    }
    samples.append(rij)
    return rij, afgebroken


# ============================================
# FASE 1 — PEILING UITREKENEN
# ============================================

def _peil_rooster(huidige_heading):
    """
    De hoeken die we aandoen: 0, 30, 60 ... 330 graden kompas.

    Absolute rasterhoeken en niet "huidige heading + n x 30": dan zijn de
    meetpunten van elke vlucht onderling vergelijkbaar, en zeggen twee
    metingen op 30° over twee vluchten hetzelfde.

    We beginnen wel bij de rasterhoek die het dichtst bij de huidige stand
    ligt en lopen van daar met de klok mee rond. Dat scheelt eenmalig tot
    180° draaien voordat de eerste meting valt.
    """
    rooster = [i * PEIL_STAP_GRADEN for i in range(PEIL_AANTAL_STAPPEN)]
    begin = min(range(PEIL_AANTAL_STAPPEN),
                key=lambda i: _hoekverschil(rooster[i], huidige_heading))
    return [rooster[(begin + k) % PEIL_AANTAL_STAPPEN]
            for k in range(PEIL_AANTAL_STAPPEN)]


def _peil_stapsgewijs(status, get_mav, mavutil, samples, melden):
    """
    Draai het rooster af en meet op elke hoek STILSTAAND.

    Per stap: draai naar de absolute hoek -> wacht tot hij bereikt is ->
    laat uitzweven -> meet PEIL_METINGEN_PER_HOEK packets -> mediaan.

    Waarom stilstaand: zie de toelichting bij PEIL_STAP_GRADEN. Kort gezegd
    komen meetwaarde en heading uit verschillende bronnen met verschillende
    ververssnelheden; alleen als de drone niet beweegt maakt dat verschil
    niets uit. De hoek die we loggen is de hoek waar hij op dat moment
    daadwerkelijk stond, niet een geschatte hoek op een geschat tijdstip.

    Een niet-bereikte hoek is geen reden om te stoppen: we meten dan op de
    hoek waar hij staat en loggen die werkelijke hoek — hetzelfde compromis
    als pattern._wacht_op_heading maakt. Beter een datapunt met de echte
    hoek dan een gat in het diagram.

    Returns (metingen, afgebroken_door_piloot, reden).
      metingen = lijst (hoek_gemeten, rssi_mediaan, snr_mediaan, n)
    """
    metingen = []
    begin = time.time()

    for stap, doel in enumerate(_peil_rooster(status.get('heading', 0.0)), 1):
        if pattern._pilot_has_taken_over(status):
            return metingen, True, ''
        if time.time() - begin > PEIL_MAX_DUUR_S:
            return metingen, False, (f'Peiling duurde langer dan '
                                     f'{PEIL_MAX_DUUR_S:.0f} s')

        melden('peilen',
               f'Fase 1/5 — peilen: hoek {doel}° '
               f'({stap}/{PEIL_AANTAL_STAPPEN})', fase=1)

        _cmd_yaw_kortste(get_mav, mavutil, doel, status.get('heading', 0.0))
        einde = time.time() + PEIL_HEADING_TIMEOUT_S
        bereikt = False
        while time.time() < einde:
            if pattern._pilot_has_taken_over(status):
                return metingen, True, ''
            if _hoekverschil(status.get('heading', 0.0),
                             doel) <= pattern.HEADING_TOLERANTIE:
                bereikt = True
                break
            time.sleep(0.1)
        if not bereikt:
            # NIET meten. Een meting midden in een draai smeert vijf packets
            # uit over ~60° heading en hoort dus bij geen enkele hoek. Een
            # gat in het diagram is eerlijker dan een vervuild punt; het
            # zwaartepunt kan tegen een ontbrekende hoek, niet tegen een
            # verkeerd gelabelde.
            print(f"[search] hoek {doel}° niet gehaald binnen "
                  f"{PEIL_HEADING_TIMEOUT_S:.0f}s (staat op "
                  f"{status.get('heading', 0.0):.1f}°) — hoek OVERGESLAGEN")
            continue

        if not pattern._wacht(SETTLE_NA_PEILSTAP_S, status):
            return metingen, True, ''

        # Meten gebeurt NA het uitzweven en op een stilhangende drone.
        rij, afgebroken = _meet_punt(status, 'peilen', samples,
                                     f'hoek {doel}°')
        if rij is not None:
            metingen.append((rij['heading'], rij['rssi'], rij['snr'], rij['n']))
            print(f"[search]   {rij['heading']:6.1f}°  RSSI {rij['rssi']:7.1f} dBm  "
                  f"SNR {rij['snr']:+5.2f} dB  (n={rij['n']})")
        else:
            print(f"[search]   {doel:6.1f}°  geen packets")
        if afgebroken:
            return metingen, True, ''

    return metingen, False, ''


def _zwaartepunt(metingen):
    """
    Richting van het zwaartepunt van de gemeten lob (vermogensgewogen
    vectorsom over alle hoeken).

    Waarom dit en niet de sterkste hoek: zie de toelichting bij
    PIEK_AFWIJKING_GRADEN. Kort: de lob is te breed en te vlak om er een
    piek uit te vissen, maar zijn zwaartepunt ligt wel op de bron.

    Weegt met 10^((snr - snr_min)/10): lineair vermogen ten opzichte van de
    zwakste gemeten richting. Zo telt de achterkant van het patroon nauwelijks
    mee zonder dat er een willekeurige constante bij hoeft.

    Returns de hoek in graden, of None als er niets te wegen valt.
    """
    if not metingen:
        return None
    snr_min = min(m[2] for m in metingen)
    x = y = 0.0
    for meting in metingen:
        hoek, snr = meting[0], meting[2]
        gewicht = 10 ** ((snr - snr_min) / 10.0)
        x += gewicht * math.cos(math.radians(hoek))
        y += gewicht * math.sin(math.radians(hoek))
    if x == 0.0 and y == 0.0:
        return None
    return math.degrees(math.atan2(y, x)) % 360


def _lob_diagnose(metingen, kandidaten):
    """
    Beschrijf de vorm van de gemeten lob. Kost geen vliegtijd — het rekent
    alleen na op wat fase 1 toch al heeft gemeten.

    Waarom dit in de vlucht zit en niet in een aparte grondtest: op de grond
    staat de operator zelf in het stralingspatroon en is de grondreflectie
    dichtbij. Een drone die op 3 m in vrije lucht hangt meet de antenne zoals
    hij werkelijk gebruikt wordt. Elke zoekvlucht is dus meteen de beste
    patroonmeting die we kunnen doen.

    De getallen die ertoe doen:
      breedte     hoeveel graden liggen binnen 3 dB van de sterkste hoek.
                  Een bruikbare richtantenne zit rond 50-70°; op de vluchten
                  van 1-8 was het ~150°.
      variatie    spreiding binnen die lob. Is die kleiner dan de ruis op een
                  mediaan van 5 packets (~1 dB), dan is de sterkste hoek
                  binnen de lob niet meer dan toeval — en dat is precies
                  waarom kandidaat 1 het zwaartepunt is en niet de piek.
      spreiding   piek versus zwaartepunt. Ver uit elkaar = brede lob of een
                  tweede bron; dicht bij elkaar = scherpe, eenduidige peiling.

    Returns een regel tekst voor de CSV-kop en het logboek.
    """
    if not metingen:
        return 'lob: geen metingen'
    if len(metingen) < 2:
        return 'lob: te weinig hoeken gemeten'

    hoeken = sorted(m[0] for m in metingen)
    # Typische hoekafstand uit de data zelf halen, niet uit PEIL_STAP_GRADEN:
    # dan klopt deze functie ook op oudere of anders bemeten reeksen.
    stappen = sorted((hoeken[(i + 1) % len(hoeken)] - hoeken[i]) % 360
                     for i in range(len(hoeken)))
    typisch = stappen[len(stappen) // 2]
    naast = typisch * 1.5

    top = max(metingen, key=lambda m: m[2])
    drempel = top[2] - 3.0
    in_lob = {m[0] for m in metingen if m[2] >= drempel}

    # Alleen de AANEENGESLOTEN lob rond de piek tellen. Een losse sterke
    # uitschieter elders in de cirkel hoort niet bij deze lob en zou de
    # breedte anders kunstmatig oprekken (op de vlucht van 17:38 maakte
    # één hoek op 292 graden er 255 graden van in plaats van ~150).
    volgorde = sorted(in_lob)
    if top[0] not in volgorde:
        volgorde.append(top[0]); volgorde.sort()
    i = volgorde.index(top[0])
    n = len(volgorde)
    lob = [volgorde[i]]
    for richting in (1, -1):
        k = i
        while True:
            vorige = volgorde[k % n]
            k += richting
            huidige = volgorde[k % n]
            if huidige in lob or len(lob) >= n:
                break
            if _hoekverschil(huidige, vorige) > naast:
                break
            lob.append(huidige)

    if len(lob) >= 2:
        gesorteerd = sorted(lob)
        gaten = [(gesorteerd[(j + 1) % len(gesorteerd)] - gesorteerd[j]) % 360
                 for j in range(len(gesorteerd))]
        breedte = 360.0 - max(gaten)
        breedte_tekst = f'{breedte:.0f}°'
    else:
        breedte = typisch
        breedte_tekst = f'<={typisch:.0f}° (1 hoek)'

    waarden = [m[2] for m in metingen if m[0] in lob]
    variatie = max(waarden) - min(waarden)

    zwaarte = _zwaartepunt(metingen)
    spreiding = _hoekverschil(top[0], zwaarte) if zwaarte is not None else 0.0
    buiten = len(in_lob) - len(lob)

    # Het oordeel gaat over één vraag: is de sterkste hoek te vertrouwen?
    #
    # Twee manieren waarop hij dat niet is, allebei op een veldvlucht gezien:
    #   1. er liggen nog andere, LOSSTAANDE richtingen binnen 3 dB van de top
    #      (17:38: de piek zat in een smal lobje op 218° terwijl negen hoeken
    #      rond 37-142° er ook binnen vielen — de beacon lag op 134°);
    #   2. de lob is zo breed en zo vlak dat de piek erbinnen ruis is
    #      (21:05: 148° breed met 1,0 dB variatie, en de ruis op het verschil
    #      tussen twee hoeken is bij een mediaan van 5 packets ook ~1 dB).
    #
    # In beide gevallen is het zwaartepunt de betere schatter, en dat is
    # precies waarom kandidaat 1 daaruit komt.
    if buiten > 0:
        oordeel = 'AMBIGU — meerdere losse richtingen binnen 3 dB'
    elif breedte > 90 and variatie < 1.5:
        oordeel = 'BREED — piek binnen de lob is ruis'
    elif breedte > 90:
        oordeel = 'BREED'
    else:
        oordeel = 'SCHERP — piek bruikbaar'

    extra = f', {buiten} losse hoek(en) erbuiten' if buiten else ''
    return (f'lob: {breedte_tekst} breed (3 dB){extra}, variatie erbinnen '
            f'{variatie:.1f} dB, piek {top[0]:.0f}° vs zwaartepunt '
            f'{zwaarte:.0f}° = {spreiding:.0f}° uiteen -> {oordeel}')


def _kandidaten(metingen):
    """
    Kies de richtingen die het proberen waard zijn.

    Kandidaat 1 is het ZWAARTEPUNT van de gemeten lob. Kandidaat 2 is de
    sterkste losse hoek, maar alleen als die meer dan PIEK_AFWIJKING_GRADEN
    van het zwaartepunt af ligt. Liggen ze dicht bij elkaar, dan zeggen ze
    hetzelfde en is een tweede verificatie zonde van de accu; liggen ze ver
    uit elkaar, dan is dat juist het teken dat er een tweede bron in het
    spel kan zijn en beslist fase 2 ertussen.

    Argument is een lijst (hoek, rssi, snr, n) uit _peil_stapsgewijs.

    Returns een lijst (hoek, snr), belangrijkste eerst, maximaal
    MAX_KANDIDATEN.
    """
    if not metingen:
        return []

    zwaarte = _zwaartepunt(metingen)
    piek = max(metingen, key=lambda m: m[2])
    if zwaarte is None:
        return [(piek[0], piek[2])]

    # SNR van de dichtstbijzijnde meting meegeven, puur als melding: het
    # zwaartepunt zelf is een berekende richting, geen meetpunt.
    dichtst = min(metingen, key=lambda m: _hoekverschil(m[0], zwaarte))
    kandidaten = [(zwaarte, dichtst[2])]

    if _hoekverschil(piek[0], zwaarte) > PIEK_AFWIJKING_GRADEN:
        kandidaten.append((piek[0], piek[2]))

    return kandidaten[:MAX_KANDIDATEN]


# ============================================
# FASE 4 — INSTORT UIT DE PASS HALEN
# ============================================

def _doorvliegen(status, get_mav, mavutil, bearing, max_m, fase, naam,
                 samples, melden, kondig):
    """
    Eén doorlopende, langzame vlucht in 'bearing' met continue meting.

    Draait EERST de neus op de koers en laat hem uitzweven, en begint pas
    daarna te loggen — zie YAW_SETTLE_VOOR_PASS_S voor waarom dat cruciaal is.

    Stopt zodra de RSSI INSTORT_LIVE_DB onder het sterkste punt van deze
    vlucht is gezakt én we er DOORVLIEG_VOORBIJ_M voorbij zijn, of bij
    max_m. Die live-stop begrenst alleen de VLUCHT; de precieze positie komt
    achteraf uit de log (_zoek_instort), zodat een enkele ruispiek de
    schatting niet kan bepalen.

    Returns (afgebroken_door_piloot, eigen_samples, afgelegd_m).
    """
    if not kondig(fase, bearing, max_m, naam):
        return True, [], 0.0

    bereikt, afgebroken = _draai_naar(status, get_mav, mavutil, bearing)
    if afgebroken:
        return True, [], 0.0
    if not bereikt:
        print(f"[search] koers {bearing:.0f}° niet gehaald vóór {naam} — "
              f"vliegt op {status.get('heading', 0.0):.1f}°")
    if not pattern._wacht(YAW_SETTLE_VOOR_PASS_S, status):
        return True, [], 0.0

    start_lat = status.get('gps_lat', 0.0)
    start_lon = status.get('gps_lon', 0.0)
    begin = len(samples)

    dn, de = _noord_oost(bearing, max_m)
    _cmd_positie_offset(get_mav, mavutil, dn, de, bearing)

    def afgelegd():
        return approach._horizontale_afstand(
            status.get('gps_lat', 0.0), status.get('gps_lon', 0.0),
            start_lat, start_lon)

    def klaar():
        # Doel bereikt?
        if afgelegd() >= max_m * 0.95:
            return True
        eigen = samples[begin:]
        if len(eigen) < 4:
            return False
        top = max(eigen, key=lambda s: s['rssi'])
        nu = eigen[-1]
        if nu['rssi'] > top['rssi'] - INSTORT_LIVE_DB:
            return False
        # Ingestort — maar pas stoppen als we er ook echt voorbij zijn.
        return approach._horizontale_afstand(
            nu['lat'], nu['lon'], top['lat'], top['lon']) >= DOORVLIEG_VOORBIJ_M

    timeout = max_m / (PASS_SNELHEID_CMS / 100.0) * 2.0 + 15.0
    afgebroken, aantal = _log_continu(status, timeout, naam, samples,
                                      klaar_fn=klaar)
    weg = afgelegd()

    # Stilhangen: _log_continu stoppen laat het positiedoel staan, dus zonder
    # dit vliegt de drone door naar het oorspronkelijke verre punt.
    if not afgebroken:
        _cmd_positie_offset(get_mav, mavutil, 0.0, 0.0, bearing)

    print(f"[search] {naam}: {aantal} packets over {weg:.1f} m")
    return afgebroken, samples[begin:], weg


def _zoek_instort(pass_samples):
    """
    Haal het passeermoment achteraf uit de gelogde pass.

    Bewust niet live: de pass moet altijd voltooien (een halverwege
    afgebroken pass geeft geen instort én geen bruikbare terugval), en de
    GPS van het instortpunt staat gewoon in de log met zijn tijdstempel.

    We zoeken de steilste SNR-daling over een venster van INSTORT_VENSTER_M
    en interpoleren de positie op het halfwaarde-punt van die daling: de
    beacon ligt binnen ~1 m van waar het signaal instort, en het midden van
    de daling ligt daar het dichtst bij.

    Drie uitkomsten, zodat de operator weet hoe hard de coördinaat is:
      hoog   - daling >= INSTORT_DALING_DB: we zijn er vlak overheen gevlogen
      midden - een zwakkere maar echte daling: we passeerden er lateraal naast
      laag   - geen daling: terugval op het punt met de hoogste RSSI (§5)

    Returns (lat, lon, betrouwbaarheid, uitleg, methode) of None als er geen
    bruikbare samples zijn. methode is 'instort' of 'rssi' — dat laatste is de
    terugval. _combineer_instorten gebruikt het om te weten hoe hard de twee
    kruisingen onderling vergelijkbaar zijn.
    """
    g = [s for s in pass_samples if s.get('rssi') is not None]
    if len(g) < 2:
        return None

    # Cumulatieve afstand langs de pass, zodat het venster in meters telt en
    # niet in samples (de sample-dichtheid hangt van de beacon-frequentie af).
    afstand = [0.0]
    for vorige, huidige in zip(g, g[1:]):
        afstand.append(afstand[-1] + approach._horizontale_afstand(
            vorige['lat'], vorige['lon'], huidige['lat'], huidige['lon']))

    beste = None   # (daling, i, j)
    for i in range(len(g)):
        for j in range(i + 1, len(g)):
            if afstand[j] - afstand[i] > INSTORT_VENSTER_M:
                break
            daling = g[i]['snr'] - g[j]['snr']
            if beste is None or daling > beste[0]:
                beste = (daling, i, j)

    if beste is None or beste[0] < INSTORT_ZWAK_DB:
        top = max(g, key=lambda s: s['rssi'])
        steilste = beste[0] if beste else 0.0
        return (top['lat'], top['lon'], 'laag',
                f"geen instort gevonden (steilste {steilste:.1f} dB over "
                f"{INSTORT_VENSTER_M:.0f} m); positie = sterkste RSSI "
                f"{top['rssi']:.0f} dBm", 'rssi')

    daling, i, j = beste
    betrouwbaarheid = 'hoog' if daling >= INSTORT_DALING_DB else 'midden'
    midden = (g[i]['snr'] + g[j]['snr']) / 2.0

    # Loop naar het eerste sample dat onder het halfwaarde-niveau zakt en
    # interpoleer daar lineair tussen de twee omliggende posities.
    lat, lon = g[j]['lat'], g[j]['lon']
    for k in range(i + 1, j + 1):
        if g[k]['snr'] <= midden:
            vorig, nu = g[k - 1], g[k]
            noemer = vorig['snr'] - nu['snr']
            f = (vorig['snr'] - midden) / noemer if noemer else 0.0
            lat = vorig['lat'] + f * (nu['lat'] - vorig['lat'])
            lon = vorig['lon'] + f * (nu['lon'] - vorig['lon'])
            break

    return (lat, lon, betrouwbaarheid,
            f"instort {daling:.1f} dB over {afstand[j] - afstand[i]:.1f} m",
            'instort')


def _combineer_instorten(heen, terug):
    """
    Voeg de instort van de heenweg en die van de terugkruising samen.

    ALTIJD middelen, ook als de twee ver uit elkaar liggen. Beide kruisingen
    meten dezelfde beacon, dus het gemiddelde van twee ruizige schattingen is
    beter dan er willekeurig één kiezen — en bij de instort-methode heft het
    middelpunt bovendien de systematische vertraging op (zie TERUGKRUISING).

    Dat was eerst anders: bij meer dan 6 m verschil weigerde deze functie te
    middelen en koos ze er één. Op de vlucht van 2-8 19:12 pakte dat slecht
    uit — de twee schattingen lagen 8,0 m uit elkaar, maar hun middelpunt lag
    3,7 m van de beacon terwijl de gekozen enkeling er 5,2 m naast zat. De
    spreiding is informatie over de ONZEKERHEID, geen reden om data weg te
    gooien; hij bepaalt daarom de betrouwbaarheid en niet of we middelen.

    Returns (lat, lon, betrouwbaarheid, uitleg, methode) of None.
    """
    goed = [r for r in (heen, terug) if r is not None]
    if not goed:
        return None
    if len(goed) == 1:
        lat, lon, zeker, uitleg, methode = goed[0]
        return (lat, lon, zeker, f'één kruising: {uitleg}', methode)

    (la, loa, za, ua, ma), (lb, lob, zb, ub, mb) = goed
    spreiding = approach._horizontale_afstand(la, loa, lb, lob)
    lat, lon = (la + lb) / 2.0, (loa + lob) / 2.0

    # Betrouwbaarheid uit twee dingen: hoe dicht de kruisingen bij elkaar
    # liggen, en of ze allebei een echte instort zagen of terugvielen op het
    # sterkste RSSI-punt.
    beide_instort = (ma == 'instort' and mb == 'instort')
    if beide_instort and spreiding <= INSTORT_VENSTER_M:
        zeker = 'hoog'
    elif spreiding <= 2 * INSTORT_VENSTER_M:
        zeker = 'midden'
    else:
        zeker = 'laag'

    methode = 'instort' if beide_instort else 'gemengd'
    return (lat, lon, zeker,
            f'middelpunt van twee kruisingen, {spreiding:.1f} m uit elkaar '
            f'(heen [{ma}]: {ua}; terug [{mb}]: {ub})',
            methode)


def _schrijf_csv(samples, stempel, hoogte, start_gps, kop):
    """
    Schrijf alle fasen in één tabel weg, met een fase-kolom. Eén bestand per
    zoekvlucht: voor de thesis moet achteraf na te lopen zijn welke meting
    tot welke beslissing leidde, en dat kan alleen als peiling, verificatie,
    nadering en pass naast elkaar staan.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    pad = os.path.join(DATA_DIR, f'search_{stempel}_h{hoogte}m.csv')

    t0 = samples[0]['t'] if samples else time.time()

    with open(pad, 'w', newline='') as f:
        f.write("# VespaTrack zoekvlucht (search.py)\n")
        f.write(f"# meetmoment: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# zoekhoogte: {hoogte} m\n")
        f.write(f"# GPS-startpositie: lat {start_gps[0]:.7f}, lon {start_gps[1]:.7f}\n")
        f.write(f"# peiling fase 1: stapsgewijs, {PEIL_AANTAL_STAPPEN} hoeken "
                f"van {PEIL_STAP_GRADEN} graden, stilstaand gemeten\n")
        f.write(f"# metingen per hoek: {PEIL_METINGEN_PER_HOEK}\n")
        f.write(f"# beacon zendvermogen: {BEACON_TX_DBM} dBm\n")
        f.write(f"# lora config: {LORA_CONFIG}\n")
        for regel in kop:
            f.write(f"# {regel}\n")
        f.write("# fase 1, 2 en 3 = STILSTAANDE medianen (1 rij per meetpunt); "
                "alleen fase 4 logt continu tijdens de pass\n")

        kolommen = ['fase', 't_s', 'tijdstip', 'gps_lat', 'gps_lon', 'gps_alt',
                    'heading', 'rssi', 'snr', 'n', 'opmerking']
        schrijver = csv.DictWriter(f, fieldnames=kolommen)
        schrijver.writeheader()
        for s in samples:
            schrijver.writerow({
                'fase': s['fase'],
                't_s': round(s['t'] - t0, 2),
                'tijdstip': s['tijdstip'],
                'gps_lat': round(s['lat'], 7),
                'gps_lon': round(s['lon'], 7),
                'gps_alt': round(s['alt'], 2),
                'heading': round(s['heading'], 1),
                'rssi': s['rssi'],
                'snr': s['snr'],
                'n': s.get('n', 1),
                'opmerking': s.get('opmerking', ''),
            })

    print(f"[search] geschreven: {pad} ({len(samples)} samples)")
    return pad


# ============================================
# DE ZOEKSEQUENTIE
# ============================================

def _run_search(status, get_mav, emit_fn, hoogte, log_fn):
    """
    De volledige zoekvlucht. Draait in een aparte thread.

    Het veiligheidsmodel loopt door de HELE functie: na elke stap en tijdens
    elke wachttijd checken we of de piloot heeft overgenomen. Elke faalweg
    eindigt in RTL met een melding die de reden noemt.

    log_fn(lat, lon, alt, notities) schrijft de gevonden positie in de
    coördinaat-log en meldt hem aan het dashboard; None = alleen CSV.
    """
    from pymavlink import mavutil

    stempel = datetime.now().strftime('%Y%m%d_%H%M%S')
    samples = []
    kop = []              # extra headerregels voor de CSV
    start_gps = (0.0, 0.0)
    gevonden = None       # (lat, lon, betrouwbaarheid, uitleg)
    snelheid_aangepast = False
    rtl_alt_aangepast = False
    afbreekreden = []     # lijst zodat de geneste helper hem kan vullen

    def melden(step, message, active=True, **extra):
        _set_state(step, message, active=active, **extra)
        emit_fn('meting_update', get_search_state())

    def afbreken_door_piloot():
        with _lock:
            search_state['aborted_by_pilot'] = True
        melden('gestopt', 'Piloot heeft overgenomen — zoekvlucht gestopt',
               active=False)

    def kondig_richting_aan(fase, bearing, afstand_m, toelichting):
        """
        Teken de volgende zet op de kaart en wacht even vóór hij uitgevoerd
        wordt.

        Dit gaat over vertrouwen in een autonoom toestel: de operator staat
        met de zender in de hand en moet kunnen zien wat het plan is voordat
        de drone vertrekt, niet erna. De lijn vertrekt vanaf de GPS-positie
        van dit moment, zodat hij ook klopt als de telemetrie iets naloopt.

        Returns False als de piloot tijdens de aankondiging overneemt.
        """
        emit_fn('search_richting', {
            'actief': True,
            'lat': status.get('gps_lat', 0.0),
            'lon': status.get('gps_lon', 0.0),
            'bearing': round(bearing % 360, 1),
            'afstand_m': round(afstand_m, 1),
            'fase': fase,
            'toelichting': toelichting,
        })
        if AANKONDIGING_S <= 0:
            return True
        return pattern._wacht(AANKONDIGING_S, status)

    def wis_richting():
        """Haal de richtingslijn van de kaart — de zet is uitgevoerd of de
        vlucht is voorbij."""
        emit_fn('search_richting', {'actief': False})

    def naar_huis(reden):
        """
        Elke faalweg loopt hier langs: RTL, met de reden in de melding.
        Nooit LOITER — dat zou de drone laten hangen tot de accu leeg is.

        De reden wordt bewaard omdat de slotmelding in het finally-blok hem
        anders overschrijft en de operator alleen "geen positie bepaald"
        overhoudt, zonder te weten waaróm.
        """
        afbreekreden.append(reden)
        kop.append(f'afgebroken: {reden}')
        melden('rtl', f'{reden} — RTL naar opstijgpunt')
        approach._cmd_rtl(get_mav)

    def accu_op():
        accu = status.get('battery_percent', -1)
        return 0 <= accu < MIN_BATTERIJ_PROCENT

    try:
        # ---- pre-flight ----
        melden('preflight', 'Pre-flight check...')

        if get_mav() is None:
            melden('fout', 'Pixhawk niet verbonden', active=False)
            return

        sats = status.get('gps_satellites', 0)
        if REQUIRE_3D_FIX and not status.get('gps_fix', False):
            melden('fout', 'Geen 3D GPS-fix — zoekvlucht afgebroken', active=False)
            return
        if sats < MIN_SATELLITES:
            melden('fout', f'Te weinig satellieten ({sats}/{MIN_SATELLITES}) — '
                           f'zoekvlucht afgebroken', active=False)
            return

        laatste = status.get('lora_last_seen_sec', -1)
        if laatste < 0 or laatste > BEACON_MAX_AGE_S:
            melden('fout', 'Geen beaconsignaal — controleer of de beacon '
                           'aanstaat en op 433,2 MHz zendt', active=False)
            return

        start_gps = (status.get('gps_lat', 0.0), status.get('gps_lon', 0.0))

        # ---- opstijgen vanaf de grond ----
        # v1 is bewust grond-takeoff only: de "start ook vanuit de lucht"
        # detectie van mission.py is voor het zoeken nooit getest en voegt
        # een tak toe die we niet kunnen valideren.
        melden('guided', 'GUIDED-mode instellen...')
        mission._cmd_set_mode_guided(get_mav, mavutil)
        if not pattern._wacht(2, status):
            afbreken_door_piloot(); return

        melden('arm', 'Armen...')
        mission._cmd_arm(get_mav, mavutil)
        if not pattern._wacht(3, status):
            afbreken_door_piloot(); return
        if not status.get('armed', False):
            melden('fout', 'Armen mislukt (check RC aan + GPS) — '
                           'zoekvlucht afgebroken', active=False)
            return

        melden('takeoff', f'Opstijgen naar {hoogte} m...')
        mission._cmd_takeoff(get_mav, mavutil, hoogte)
        deadline = time.time() + TAKEOFF_TIMEOUT_S
        while time.time() < deadline:
            if pattern._pilot_has_taken_over(status):
                afbreken_door_piloot(); return
            if status.get('altitude', 0) >= hoogte * 0.95:
                break
            time.sleep(0.3)
        if not pattern._wacht(SETTLE_NA_KLIM_S, status):
            afbreken_door_piloot(); return

        # ---- hovertest: deugt het toestel? ----
        # Nu, op zoekhoogte en boven het opstijgpunt: deugt het niet, dan
        # landen we waar we staan zonder ook maar één meter te vliegen.
        melden('hovertest', 'Toestandscontrole — stilhangen en meten...')
        gezond, oordeel, hovermeting = _hovertest(status)
        kop.append(f'hovertest: {oordeel}')
        print(f'[search] hovertest: {oordeel}')
        if not gezond:
            if pattern._pilot_has_taken_over(status):
                afbreken_door_piloot(); return
            # LAND en niet RTL: we staan nog boven het opstijgpunt, en bij een
            # mechanisch probleem is recht naar beneden op de bekende plek
            # veiliger dan eerst ergens heen vliegen.
            afbreekreden.append(f'Hovertest afgekeurd — {oordeel}')
            melden('landen', f'Hovertest afgekeurd — {oordeel} — LANDEN',
                   fase=0)
            mission._cmd_land(get_mav, mavutil)
            return
        melden('hovertest', f'Toestand OK — {oordeel}')

        # ================================================
        # FASE 1 — PEILEN
        # ================================================
        # STAPSGEWIJS, niet in één doorlopende draai. De reden staat
        # uitgebreid bij PEIL_STAP_GRADEN; kort: bij een continue draai komen
        # meetwaarde en heading uit bronnen met verschillende ververssnelheden
        # en hoort een rij dus niet gegarandeerd bij één hoek. Stilstaand
        # meten maakt die vraag irrelevant.
        metingen, afgebroken, reden = _peil_stapsgewijs(
            status, get_mav, mavutil, samples, melden)
        if afgebroken:
            afbreken_door_piloot(); return

        met_signaal = len(metingen)
        kandidaten = _kandidaten(metingen)
        kop.append(f'fase 1: stapsgewijs, {PEIL_AANTAL_STAPPEN} hoeken x '
                   f'{PEIL_STAP_GRADEN}°, {met_signaal} met signaal')

        print(f"[search] peiling: {met_signaal}/{PEIL_AANTAL_STAPPEN} hoeken "
              f"met signaal")

        if reden:
            naar_huis(reden)
            return
        if met_signaal == 0:
            naar_huis('Geen enkel packet tijdens het peilen')
            return
        if met_signaal < MIN_HOEKEN_MET_SIGNAAL:
            naar_huis(f'Slechts {met_signaal} van {PEIL_AANTAL_STAPPEN} hoeken '
                      f'gaven signaal — peiling niet betrouwbaar')
            return
        if not kandidaten:
            naar_huis('Peiling gaf geen bruikbare richting')
            return

        kop.append('kandidaten: ' + ', '.join(
            f'{h:.0f}° ({s:+.1f} dB)' for h, s in kandidaten))

        # Elke vlucht is meteen een antennepatroon-meting: hier staat hoe
        # scherp de lob was, en dus hoeveel de peiling waard is.
        diagnose = _lob_diagnose(metingen, kandidaten)
        kop.append(diagnose)
        print(f"[search] {diagnose}")
        melden('peilen', f'Fase 1/5 — {diagnose}', fase=1)

        if not pattern._wacht(SETTLE_NA_DRAAI_S, status):
            afbreken_door_piloot(); return

        # ================================================
        # FASE 2 — VERIFIËREN
        # ================================================
        # De yaw wordt hier vastgezet op de kandidaatrichting en blijft dat
        # de rest van de vlucht: alleen bij een constante antenne-oriëntatie
        # zegt een RSSI-verschil tussen twee punten iets over afstand.
        bearing = None
        for nummer, (hoek, snr) in enumerate(kandidaten, start=1):
            if accu_op():
                naar_huis(f"Batterij {status.get('battery_percent')}%")
                return

            melden('verifieren',
                   f'Fase 2/5 — kandidaat {nummer}/{len(kandidaten)} op '
                   f'{hoek:.0f}° verifiëren', fase=2)

            # Aankondigen vóór het draaien, niet vóór de stap: de operator
            # ziet de lijn dan al staan terwijl de neus nog moet draaien en
            # de nulmeting nog loopt — ruim de meeste voorsprong die er is.
            if not kondig_richting_aan(
                    2, hoek, VERIFICATIE_STAP_M,
                    f'kandidaat {nummer}/{len(kandidaten)} verifiëren'):
                afbreken_door_piloot(); return

            # Eerst de neus op de kandidaat, dan pas meten: anders zit het
            # antenne-effect van de draai in het voor/na-verschil.
            bereikt, afgebroken = _draai_naar(status, get_mav, mavutil, hoek)
            if afgebroken:
                afbreken_door_piloot(); return
            if not bereikt:
                print(f"[search] heading {hoek:.0f}° niet gehaald binnen "
                      f"{DRAAI_TIMEOUT_S}s — meet op "
                      f"{status.get('heading', 0.0):.1f}°")
            if not pattern._wacht(SETTLE_NA_DRAAI_S, status):
                afbreken_door_piloot(); return

            voor, afgebroken = _meet_punt(
                status, 'verifieren', samples, f'kandidaat {hoek:.0f}° voor')
            if afgebroken:
                afbreken_door_piloot(); return
            if voor is None:
                naar_huis('Geen packets meer — beacon uitgevallen?')
                return

            # Al te dichtbij om te verifiëren? Dan is de RSSI verzadigd en
            # zou 5 m geen 3 dB opleveren; de echte bron zou onterecht
            # verworpen worden.
            if voor['rssi'] >= VERIFY_OVERSLAAN_RSSI:
                bearing = hoek
                kop.append(f'fase 2: overgeslagen, RSSI {voor["rssi"]:.0f} dBm '
                           f'>= {VERIFY_OVERSLAAN_RSSI:.0f} dBm (al dichtbij)')
                melden('verifieren',
                       f'Fase 2/5 — signaal al sterk ({voor["rssi"]:.0f} dBm), '
                       f'verificatie overgeslagen', fase=2)
                break

            start_lat = status.get('gps_lat', 0.0)
            start_lon = status.get('gps_lon', 0.0)
            dn, de = _noord_oost(hoek, VERIFICATIE_STAP_M)
            _cmd_positie_offset(get_mav, mavutil, dn, de, hoek)
            voltooid, afgebroken = approach._wacht_op_stap(
                status, start_lat, start_lon, VERIFICATIE_STAP_M)
            if afgebroken:
                afbreken_door_piloot(); return
            if not voltooid:
                print(f"[search] verificatiestap niet bevestigd binnen timeout "
                      f"— meet op de huidige positie")
            if not pattern._wacht(SETTLE_NA_STAP_S, status):
                afbreken_door_piloot(); return

            na, afgebroken = _meet_punt(
                status, 'verifieren', samples, f'kandidaat {hoek:.0f}° na')
            if afgebroken:
                afbreken_door_piloot(); return
            if na is None:
                naar_huis('Geen packets meer — beacon uitgevallen?')
                return

            stijging = na['rssi'] - voor['rssi']
            print(f"[search] kandidaat {hoek:.0f}°: RSSI {voor['rssi']:.0f} -> "
                  f"{na['rssi']:.0f} dBm ({stijging:+.1f} dB)")

            if stijging >= VERIFY_STIJGING_DB:
                bearing = hoek
                kop.append(f'fase 2: kandidaat {hoek:.0f}° bevestigd '
                           f'({stijging:+.1f} dB over {VERIFICATIE_STAP_M} m)')
                break

            # Verworpen: dit is waarschijnlijk een reflectie. Terug naar het
            # startpunt zodat de volgende kandidaat vanaf dezelfde plek
            # vertrekt en zijn peil-bearing nog klopt.
            kop.append(f'fase 2: kandidaat {hoek:.0f}° verworpen '
                       f'({stijging:+.1f} dB)')
            melden('verifieren',
                   f'Kandidaat {hoek:.0f}° verworpen ({stijging:+.1f} dB) — '
                   f'terug naar startpunt', fase=2)
            if not kondig_richting_aan(2, hoek + 180, VERIFICATIE_STAP_M,
                                       'terug naar startpunt'):
                afbreken_door_piloot(); return

            terug_lat = status.get('gps_lat', 0.0)
            terug_lon = status.get('gps_lon', 0.0)
            _cmd_positie_offset(get_mav, mavutil, -dn, -de, hoek)
            _, afgebroken = approach._wacht_op_stap(
                status, terug_lat, terug_lon, VERIFICATIE_STAP_M)
            if afgebroken:
                afbreken_door_piloot(); return
            if not pattern._wacht(SETTLE_NA_STAP_S, status):
                afbreken_door_piloot(); return

        if bearing is None:
            geprobeerd = ', '.join(f'{h:.0f}°' for h, _ in kandidaten)
            naar_huis(f'Alle kandidaten verworpen ({geprobeerd})')
            return

        # ================================================
        # FASE 3 — DOORVLIEGEN
        # ================================================
        # Eén doorlopende, langzame vlucht in de bevestigde richting, met
        # continue meting. Geen stappen en geen koerscorrectie meer: zie de
        # toelichting bij DOORVLIEG_MAX_M. De yaw wordt vóór het loggen
        # uitgedraaid, anders meet de eerste helft van de reeks de draai.
        if accu_op():
            naar_huis(f"Batterij {status.get('battery_percent')}%")
            return

        melden('doorvliegen',
               f'Fase 3/5 — doorvliegen op {bearing:.0f}° '
               f'({PASS_SNELHEID_CMS / 100:.1f} m/s, max {DOORVLIEG_MAX_M:.0f} m)',
               fase=3)

        _cmd_set_param(get_mav, mavutil, 'WPNAV_SPEED', PASS_SNELHEID_CMS)
        snelheid_aangepast = True
        if not pattern._wacht(1.0, status):
            afbreken_door_piloot(); return

        afgebroken, heen_samples, heen_m = _doorvliegen(
            status, get_mav, mavutil, bearing, DOORVLIEG_MAX_M,
            3, 'doorvliegen', samples, melden, kondig_richting_aan)
        if afgebroken:
            afbreken_door_piloot(); return

        instort_heen = _zoek_instort(heen_samples)
        kop.append(f'fase 3: doorgevlogen {heen_m:.1f} m, '
                   f'{len(heen_samples)} packets')
        if instort_heen:
            kop.append(f'fase 3: heenweg — {instort_heen[3]}')

        # ================================================
        # FASE 4 — TERUGKRUISING
        # ================================================
        # Dezelfde lijn terug. Twee kruisingen uit tegengestelde richting
        # heffen een systematische meetvertraging op; zie TERUGKRUISING.
        # De terugweg is bovendien grotendeels de thuisreis.
        instort_terug = None
        if TERUGKRUISING and not accu_op():
            terug_bearing = (bearing + 180.0) % 360.0
            terug_max = min(DOORVLIEG_MAX_M, heen_m + DOORVLIEG_VOORBIJ_M)
            melden('terugkruisen',
                   f'Fase 4/5 — terugkruising op {terug_bearing:.0f}° '
                   f'(max {terug_max:.0f} m)', fase=4)

            afgebroken, terug_samples, terug_m = _doorvliegen(
                status, get_mav, mavutil, terug_bearing, terug_max,
                4, 'terugkruisen', samples, melden, kondig_richting_aan)
            if afgebroken:
                afbreken_door_piloot(); return

            instort_terug = _zoek_instort(terug_samples)
            kop.append(f'fase 4: teruggekruist {terug_m:.1f} m, '
                       f'{len(terug_samples)} packets')
            if instort_terug:
                kop.append(f'fase 4: terugweg — {instort_terug[3]}')
        elif accu_op():
            kop.append(f'fase 4: terugkruising overgeslagen, batterij '
                       f'{status.get("battery_percent")}%')

        gevonden = _combineer_instorten(instort_heen, instort_terug)
        if gevonden:
            kop.append(f'resultaat: {gevonden[3]}')

        # ================================================
        # FASE 5 — AFRONDEN
        # ================================================
        _cmd_set_param(get_mav, mavutil, 'WPNAV_SPEED', STANDAARD_SNELHEID_CMS)
        snelheid_aangepast = False

        # RTL op ZOEKHOOGTE in plaats van eerst klimmen naar RTL_ALT (5 m).
        # Er is niets te winnen met hoogte op de terugweg: de drone komt van
        # een punt dat hij zelf net overvlogen heeft, dus de route is vrij.
        # Lager terugkomen scheelt accu en beperkt de val als er onderweg
        # alsnog iets bezwijkt. We zetten RTL_ALT op de zoekhoogte en niet op
        # 0 ("huidige hoogte"): mocht het terugzetten in het finally-blok
        # mislukken, dan klimt een RTL van de piloot nog altijd naar 2,5 m in
        # plaats van vlak over de grond terug te vliegen.
        _cmd_set_param(get_mav, mavutil, 'RTL_ALT', hoogte * 100.0)
        rtl_alt_aangepast = True
        if not pattern._wacht(0.5, status):
            afbreken_door_piloot(); return

        if gevonden:
            melden('rtl', f'Fase 5/5 — beacon gevonden ({gevonden[2]}) — '
                          f'terug naar opstijgpunt op {hoogte} m', fase=5)
        else:
            melden('rtl', f'Fase 5/5 — geen positie bepaald — '
                          f'terug naar opstijgpunt op {hoogte} m', fase=5)
        approach._cmd_rtl(get_mav)

    except Exception as e:
        melden('fout', f'Fout tijdens zoekvlucht: {e}', active=False)
        print(f"[search] FOUT: {e}")

    finally:
        # Wat er ook gebeurd is: snelheid terugzetten, wegschrijven wat we
        # hebben, en de positie loggen als we er een hebben.

        # De richtingslijn hoort bij een zet die nog moet komen. Er komt er
        # geen meer, dus hij moet van de kaart — ook na een afbreking, anders
        # blijft er een plan staan dat niet meer uitgevoerd wordt.
        try:
            wis_richting()
        except Exception as e:
            print(f"[search] richtingslijn wissen mislukt: {e}")

        # WPNAV_SPEED terugzetten gebeurt óók als de piloot heeft overgenomen.
        # Dat is geen stuurcommando en botst dus niet met "de zender is
        # primair": het zet alleen een instelling terug. Zou hij op 0,5 m/s
        # blijven staan, dan vliegt ook de RTL van de piloot half zo snel, en
        # dat kost precies de accu die hij dan nodig heeft.
        if snelheid_aangepast or rtl_alt_aangepast:
            try:
                from pymavlink import mavutil as _mavutil
                if snelheid_aangepast:
                    _cmd_set_param(get_mav, _mavutil, 'WPNAV_SPEED',
                                   STANDAARD_SNELHEID_CMS)
                if rtl_alt_aangepast:
                    _cmd_set_param(get_mav, _mavutil, 'RTL_ALT',
                                   STANDAARD_RTL_ALT_CM)
            except Exception as e:
                print(f"[search] parameters terugzetten mislukt: {e}")

        # Ook wegschrijven als er geen meetpunten zijn maar wel een kop: een
        # afgekeurde hovertest levert nul samples op, terwijl juist dán de
        # gemeten trilling en motorspreiding bewaard moeten blijven.
        if samples or kop:
            _schrijf_csv(samples, stempel, hoogte, start_gps, kop)

        melding = ''
        if gevonden:
            lat, lon, betrouwbaarheid, uitleg = gevonden[:4]
            melding = (f'beacon op {lat:.7f}, {lon:.7f} '
                       f'(betrouwbaarheid {betrouwbaarheid})')
            print(f"[search] --- Resultaat ---")
            print(f"[search]   positie: {lat:.7f}, {lon:.7f}")
            print(f"[search]   {uitleg}")
            if log_fn is not None:
                try:
                    log_fn(lat, lon, 0,
                           f'Zoekvlucht {stempel} — {uitleg} '
                           f'(betrouwbaarheid {betrouwbaarheid})')
                except Exception as e:
                    print(f"[search] coördinaat-log schrijven mislukt: {e}")

        with _lock:
            afgebroken = search_state['aborted_by_pilot']
            fout = search_state['step'] == 'fout'

        if not afgebroken and not fout:
            if afbreekreden:
                _set_state('klaar', f'Zoekvlucht beëindigd — {afbreekreden[0]}',
                           active=False)
            else:
                _set_state('klaar', f'Zoekvlucht voltooid — '
                                    f'{melding or "geen positie bepaald"}',
                           active=False)
        else:
            with _lock:
                search_state['active'] = False

        emit_fn('meting_update', get_search_state())


# ============================================
# PUBLIEKE API (aangeroepen vanuit app.py)
# ============================================

def start_search(status, get_mav, emit_fn, hoogte=None, log_fn=None):
    """
    Start de zoekvlucht in een aparte thread.

    Weigert te starten als er al een zoekvlucht, rotatiemeting, nadering of
    demo-missie loopt: twee threads die tegelijk MAVLink naar dezelfde drone
    sturen is niet iets wat je in de lucht wilt uitzoeken.

    De hoogte komt uit het bestaande zoekhoogte-veld van het dashboard en
    wordt geclampt door mission._clamp_altitude — dezelfde grens als de
    demo-missie, dus dezelfde geofence-marge.

    Returns (success: bool, message: str) voor directe feedback.
    """
    global _thread

    if pattern.get_meting_state().get('active'):
        return False, 'Er loopt een rotatiemeting — wacht tot die klaar is'
    if approach.get_approach_state().get('active'):
        return False, 'Er loopt een nadering — wacht tot die klaar is'
    if mission.get_mission_state().get('active'):
        return False, 'Er loopt een demo-missie — wacht tot die klaar is'

    h = mission._clamp_altitude(hoogte)

    # 'active' meteen claimen, in hetzelfde lock als de check: zou de thread
    # hem pas bij zijn eerste statusmelding zetten, dan start een tweede klik
    # binnen die paar milliseconden een tweede zoekvlucht — twee threads die
    # tegelijk MAVLink naar dezelfde drone sturen.
    with _lock:
        if search_state['active']:
            return False, 'Er loopt al een zoekvlucht'
        search_state['active'] = True
        search_state['aborted_by_pilot'] = False
        search_state['step'] = 'start'
        search_state['hoogte'] = h
        search_state['fase'] = 0

    _thread = threading.Thread(
        target=_run_search,
        args=(status, get_mav, emit_fn, h, log_fn),
        daemon=True,
        name='zoekvlucht'
    )
    try:
        _thread.start()
    except RuntimeError as e:
        with _lock:
            search_state['active'] = False
        return False, f'Zoekvlucht kon niet starten: {e}'
    return True, f'Zoekvlucht gestart (zoekhoogte {h} m)'
