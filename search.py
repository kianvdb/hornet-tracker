#!/usr/bin/env python3
"""
search.py — autonoom zoekalgoritme: lokaliseer de LoRa-beacon en log de positie.

Dit is de eerste OPERATIONELE functie van VespaTrack. pattern.py en approach.py
waren meetinstrumenten om te leren hoe het signaal zich gedraagt; deze module
gebruikt die kennis om het nest te vinden en de coördinaat vast te leggen voor
de verdelger. Het volledige ontwerp met de herkomst van elke parameter staat in
docs/zoekalgoritme-ontwerp.md — dat document is de bron, deze code volgt het.

DE VIJF FASEN

    1. peilen      continue draai (4°/s), elk packet gelogd met de heading van
                   dat moment; achteraf mediaan per 15°-venster -> kandidaten
    2. verifiëren  5 m in de kandidaatrichting; RSSI moet >= 3 dB stijgen,
                   anders is het een reflectie en proberen we de volgende
    3. naderen     5 m-stappen, na elke stap de KOERS corrigeren naar
                   stijgende RSSI (niet blind de peil-bearing volgen)
    4. overvliegen één gladde pass op 0,5 m/s met continue meting; de
                   signaal-instort wordt ACHTERAF uit de log gehaald
    5. afronden    RTL + coördinaat-log-entry + toast

WAAROM DE KOERS CORRIGEERT MAAR DE NEUS NIET
De peiling is maar nauwkeurig op ±30° (= ±7,5 m op 15 m afstand). Blind die
bearing volgen laat de overvlieg-pass de beacon lateraal missen. Daarom
corrigeert fase 3 de VLIEGRICHTING naar stijgende RSSI. De YAW blijft
ondertussen vast op de peil-bearing: de antenne is richtingsgevoelig, dus
alleen bij een constante oriëntatie zeggen RSSI-verschillen tussen stappen
iets over afstand in plaats van over hoe de drone toevallig gedraaid staat.
Dat is ook waarom hier een positie-setpoint met vast yaw-veld nodig was, dat
mission.py nog niet had.

WAAROM NIET pattern._verzamel_metingen VOOR FASE 1 EN 4
Die meet stilstaand N samples. Fase 1 en 4 meten tijdens BEWEGING en moeten
elk packet koppelen aan de heading/GPS van dat moment — dat is _log_continu
hieronder. Voor de stilstaande meetpunten in fase 2 en 3 wordt pattern's
functie wél hergebruikt.

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
# FASE 1 — PEILEN
# ============================================

# Draaisnelheid. Bepaalt zowel de filtering als het accubudget: bij een
# beacon op 2 Hz krijg je 2 x (15/w) metingen per 15°-venster, dus langzamer
# draaien = meer metingen = robuustere mediaan. 3°/s filtert het best maar
# laat te weinig marge voor een verworpen kandidaat; 6°/s spaart accu maar
# geeft nog maar 5 metingen per venster. 4°/s is de middenweg uit ontwerp §6.
ROTATIE_SNELHEID_DPS = 4.0
ROTATIE_GRADEN       = 360
ROTATIE_MARGE_S      = 10.0   # extra wachttijd bovenop 360/snelheid

# Hoekvensters waarover de mediaan gaat. 15° is grof genoeg om ~7-8 metingen
# te bevatten bij 4°/s en fijn genoeg t.o.v. de ±30° peilnauwkeurigheid.
HOEKVENSTER_GRADEN      = 15
MIN_SAMPLES_PER_VENSTER = 3    # minder = venster te dun om op te vertrouwen

# Draaide hij écht rond? Als de yaw-opdracht niet is uitgevoerd, staan alle
# samples in een handvol vensters en is de "peiling" betekenisloos. Dan is
# doorvliegen erger dan stoppen.
MIN_HOEKDEKKING_GRADEN = 270

# Kandidaat-marge: de boomrand-reflectie lag 2 dB onder de beacon in de
# rotatiemetingen, dus alles binnen 2 dB van de sterkste richting kan de
# echte bron zijn en verdient een verificatie.
KANDIDAAT_MARGE_DB = 2.0

# Hoeveel kandidaten we maximaal proberen. Elke verworpen kandidaat kost
# ~40 s en het accubudget (ontwerp §6) draagt er precies één bij 4°/s.
MAX_KANDIDATEN = 2


# ============================================
# FASE 2 — VERIFIËREN
# ============================================

VERIFICATIE_STAP_M = 5.0   # RSSI stijgt ~0,8 dB/m -> 5 m geeft ~4 dB
VERIFY_STIJGING_DB = 3.0   # onder de verwachte 4 dB, boven de ruis (2,5 dB)

# Boven deze RSSI slaan we de verificatie over: de drone staat dan al zo
# dichtbij dat de RSSI verzadigd is en over 5 m geen 3 dB meer kan stijgen —
# verify zou de echte bron onterecht verwerpen. Uit de naderingsmeting
# (log 165): -93 dBm op 15 m, -86 dBm op 6 m. -88 dBm ligt op ~7-8 m, net
# vóór de verzadiging.
#
# Dit is een GROVE afstandsschatting en wordt daarom alleen gebruikt om een
# verificatie over te slaan (een fout kost hooguit een overbodige of gemiste
# controlestap). Als aankomstcriterium in fase 3 deugt een absolute RSSI
# niet — zie TOP_MARGE_DB.
VERIFY_OVERSLAAN_RSSI = -88.0


# ============================================
# FASE 3 — NADEREN
# ============================================

NADERING_STAP_M = 5.0

# Minimale RSSI-stijging per stap om "we lopen de goede kant op" te mogen
# concluderen. Ligt onder de verwachte ~4 dB per 5 m en boven wat een
# mediaan van 5 packets aan ruis overhoudt.
MIN_STIJGING_DB = 1.5

# Koerscorrectie als de RSSI niet steeg. Vaste amplitude, en elke kant maar
# één keer: eerst +30°, daarna -60° (= 30° aan de andere kant van de
# oorspronkelijke koers). Zo kan de correctie per definitie niet oscilleren,
# en 30° dekt de ±30° peilonnauwkeurigheid.
GRADIENT_CORRECTIE_GRADEN = (30.0, -60.0)
MAX_CORRECTIES            = len(GRADIENT_CORRECTIE_GRADEN)

# Bovengrens op het aantal naderingsstappen. Van 25 m naar 6 m zijn er 4
# nodig; meer dan 6 betekent dat we niet convergeren en de accu opmaken.
MAX_NADERINGSSTAPPEN = 6

# Hoe dicht bij het sterkste punt van de hele nadering een meetpunt moet
# liggen om als "op de top van de heuvel" te tellen.
#
# Dit vervangt een absolute RSSI-drempel als aankomstcriterium, en dat is
# geen detail: in de rotatiemetingen varieert de RSSI 9-11 dB over de HOEK,
# terwijl het hele afstandsbereik 15 -> 6 m maar ~7 dB oplevert. Een vaste
# dBm-grens zegt dus meer over hoe de drone gedraaid staat dan over hoe ver
# de beacon is. Vergelijken met het eigen beste punt haalt die hoekterm eruit,
# want de yaw ligt tijdens de hele nadering vast.
TOP_MARGE_DB = 1.0

# Onder ~6 m stijgt de RSSI niet meer (verzadiging). Dat is tegelijk het
# stopcriterium van fase 3 en de aanname over hoe ver de beacon dan nog
# vooruit ligt voor fase 4.
SATURATIE_AFSTAND_M = 6.0


# ============================================
# FASE 4 — OVERVLIEGEN
# ============================================

PASS_VOORBIJ_M = 5.0   # hoever voorbij de geschatte positie de pass doorloopt

# Tijdens de pass zetten we WPNAV_SPEED tijdelijk op 50 cm/s: bij 2 Hz geeft
# dat een sample elke 25 cm, fijn genoeg om een instort van ~1 m te zien.
# Na de pass zetten we hem terug op de waarde uit de paramdump (100 = 1 m/s);
# blijft hij op 50 staan, dan wordt ook de RTL half zo snel en dat kost accu.
PASS_SNELHEID_CMS      = 50.0
STANDAARD_SNELHEID_CMS = 100.0

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

    Hergebruikt pattern._cmd_yaw_absoluut: absoluut en niet relatief, zodat
    de fout zich niet opstapelt over meerdere kandidaten.

    Returns (bereikt, afgebroken_door_piloot). Een niet-gehaalde hoek is geen
    reden om te stoppen — we meten dan op de hoek waar hij staat, en die
    staat in de log.
    """
    pattern._cmd_yaw_absoluut(get_mav, mavutil, doel_graden)
    einde = time.time() + DRAAI_TIMEOUT_S
    while time.time() < einde:
        if pattern._pilot_has_taken_over(status):
            return False, True
        if _hoekverschil(status.get('heading', 0.0),
                         doel_graden) <= pattern.HEADING_TOLERANTIE:
            return True, False
        time.sleep(0.1)
    return False, False


def _log_continu(status, duur_s, fase, samples, klaar_fn=None):
    """
    Log elk binnenkomend LoRa-packet met de heading en GPS van dat moment,
    terwijl de drone beweegt. Dit is wat pattern._verzamel_metingen NIET kan:
    die meet stilstaand een vast aantal samples.

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

def _vensters(samples):
    """
    Verdeel de continu gelogde draai-samples in hoekvensters en neem de
    mediaan-SNR per venster.

    SNR en niet RSSI: het hoekcontrast in de rotatiemetingen zat in de SNR
    (dip -2 dB, piek +7 dB). RSSI is de afstandsmaat en komt pas in fase 2-3
    aan bod.

    Returns een lijst (midden_hoek, snr_mediaan, aantal), gesorteerd op hoek.
    Vensters met te weinig samples vallen af: die zijn te dun om een mediaan
    op te bouwen die tegen de waargenomen uitschieters bestand is.
    """
    bakken = {}
    for s in samples:
        index = int(s['heading'] % 360 // HOEKVENSTER_GRADEN)
        bakken.setdefault(index, []).append(s['snr'])

    resultaat = []
    for index, waarden in sorted(bakken.items()):
        if len(waarden) < MIN_SAMPLES_PER_VENSTER:
            continue
        midden = index * HOEKVENSTER_GRADEN + HOEKVENSTER_GRADEN / 2.0
        resultaat.append((midden, round(statistics.median(waarden), 2),
                          len(waarden)))
    return resultaat


def _hoekdekking(samples):
    """
    Hoeveel graden van de cirkel zijn daadwerkelijk bemeten?

    Als de yaw-opdracht niet is uitgevoerd staan alle samples in een handvol
    vensters; de "peiling" is dan de richting waar de drone toevallig naar
    keek. Dat is gevaarlijker dan geen peiling, dus we meten het.
    """
    indexen = {int(s['heading'] % 360 // HOEKVENSTER_GRADEN) for s in samples}
    return len(indexen) * HOEKVENSTER_GRADEN


def _kandidaten(vensters):
    """
    Kies de richtingen die het proberen waard zijn.

    De sterkste richting plus alles binnen KANDIDAAT_MARGE_DB daarvan — de
    boomrand-reflectie lag 2 dB onder de beacon, dus binnen die marge kan de
    echte bron zitten. Naïef "hoogste piek" faalt aantoonbaar: op de
    4 m-vlucht koos dat stap 32 (105°) terwijl de beacon op 30° stond, omdat
    de plateau-piek 0,3 dB boven de beacon-richting lag.

    Aangrenzende vensters worden samengevoegd tot één lob: een bundel van
    ±30° vult meerdere 15°-vensters, en die apart verifiëren zou de accu
    opmaken aan vier keer dezelfde richting. Per lob houden we het sterkste
    venster over.

    Returns een lijst (hoek, snr), sterkste eerst, maximaal MAX_KANDIDATEN.
    """
    if not vensters:
        return []

    beste_snr = max(v[1] for v in vensters)
    in_marge = [v for v in vensters if v[1] >= beste_snr - KANDIDAAT_MARGE_DB]
    in_marge.sort(key=lambda v: v[0])

    # Clusteren op aangrenzende hoeken; het laatste en eerste venster kunnen
    # over 0° aan elkaar grenzen.
    clusters = []
    for venster in in_marge:
        if clusters and _hoekverschil(venster[0], clusters[-1][-1][0]) <= HOEKVENSTER_GRADEN * 1.5:
            clusters[-1].append(venster)
        else:
            clusters.append([venster])
    if (len(clusters) > 1
            and _hoekverschil(clusters[0][0][0], clusters[-1][-1][0]) <= HOEKVENSTER_GRADEN * 1.5):
        clusters[0] = clusters[-1] + clusters[0]
        clusters.pop()

    toppen = [max(c, key=lambda v: v[1]) for c in clusters]
    toppen.sort(key=lambda v: v[1], reverse=True)
    return [(t[0], t[1]) for t in toppen[:MAX_KANDIDATEN]]


# ============================================
# FASE 4 — INSTORT UIT DE PASS HALEN
# ============================================

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

    Returns (lat, lon, betrouwbaarheid, uitleg) of None als er geen bruikbare
    samples zijn.
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
                f"{top['rssi']:.0f} dBm")

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
            f"instort {daling:.1f} dB over {afstand[j] - afstand[i]:.1f} m")


# ============================================
# UITVOER
# ============================================

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
        f.write(f"# rotatiesnelheid fase 1: {ROTATIE_SNELHEID_DPS} graden/s\n")
        f.write(f"# beacon zendvermogen: {BEACON_TX_DBM} dBm\n")
        f.write(f"# lora config: {LORA_CONFIG}\n")
        for regel in kop:
            f.write(f"# {regel}\n")
        f.write("# fase 1 en 4 = continue samples tijdens beweging; "
                "fase 2 en 3 = stilstaande medianen\n")

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
    afbreekreden = []     # lijst zodat de geneste helper hem kan vullen

    def melden(step, message, active=True, **extra):
        _set_state(step, message, active=active, **extra)
        emit_fn('meting_update', get_search_state())

    def afbreken_door_piloot():
        with _lock:
            search_state['aborted_by_pilot'] = True
        melden('gestopt', 'Piloot heeft overgenomen — zoekvlucht gestopt',
               active=False)

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

        # ================================================
        # FASE 1 — PEILEN
        # ================================================
        draai_duur = ROTATIE_GRADEN / ROTATIE_SNELHEID_DPS
        melden('peilen',
               f'Fase 1/5 — peilen: {ROTATIE_GRADEN}° draaien op '
               f'{ROTATIE_SNELHEID_DPS}°/s ({draai_duur:.0f} s)',
               hoogte=hoogte, fase=1)

        mission._cmd_yaw(get_mav, mavutil, ROTATIE_GRADEN, ROTATIE_SNELHEID_DPS)
        afgebroken, aantal = _log_continu(
            status, draai_duur + ROTATIE_MARGE_S, 'peilen', samples)
        if afgebroken:
            afbreken_door_piloot(); return

        dekking = _hoekdekking(samples)
        vensters = _vensters(samples)
        kandidaten = _kandidaten(vensters)
        kop.append(f'fase 1: {aantal} packets, hoekdekking {dekking}°, '
                   f'{len(vensters)} bruikbare vensters')

        print(f"[search] peiling: {aantal} packets, dekking {dekking}°")
        for hoek, snr, n in vensters:
            print(f"[search]   {hoek:5.1f}°  SNR {snr:+.2f} dB  (n={n})")

        if aantal == 0:
            naar_huis('Geen enkel packet tijdens het peilen')
            return
        if dekking < MIN_HOEKDEKKING_GRADEN:
            naar_huis(f'Draai niet uitgevoerd (slechts {dekking}° bemeten)')
            return
        if not kandidaten:
            naar_huis('Peiling gaf geen bruikbare richting')
            return

        kop.append('kandidaten: ' + ', '.join(
            f'{h:.0f}° ({s:+.1f} dB)' for h, s in kandidaten))
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
        # FASE 3 — NADEREN OP RSSI-GRADIËNT
        # ================================================
        # YAW-KEUZE PER FASE — het waarom, want het is niet vanzelfsprekend.
        #
        # De rotatiemetingen laten zien dat de antenne 9-11 dB RSSI-verschil
        # maakt tussen op-as en van-as. Dat is MEER dan de ~7 dB die het hele
        # afstandsbereik 15 -> 6 m oplevert. De neusrichting is dus geen
        # detail: hij bepaalt grotendeels wat je meet.
        #
        #   fase 2 — yaw VAST op de kandidaatrichting. Voor en na de 5 m-stap
        #            staat de antenne identiek, dus het verschil van 3 dB is
        #            zuiver afstand. Dat is precies wat de test moet toetsen.
        #   fase 3 — yaw VOLGT de koers. Zo wijst de antenne altijd waar we
        #            heen vliegen en is de RSSI maximaal als de koers naar de
        #            bron wijst: de koerscorrectie zoekt dan tegelijk de
        #            richting én de afstand. Met een vaste yaw draait de bron
        #            juist uit de lob zodra we scheef lopen, en klim je op een
        #            hoekhelling in plaats van een afstandshelling — daar liep
        #            de simulatie op vast, op 5 tot 8 m van de beacon.
        #   fase 4 — yaw VAST langs de pass-richting. De pass is één rechte
        #            lijn, dus de oriëntatie verandert toch niet, en de
        #            signaal-instort blijft zuiver geometrisch.
        #
        # De heading-drift die de naderingsmeting scheeftrok komt hier niet
        # terug: die kwam van BODY-frame navigatie ("vlieg vooruit"), en wij
        # commanderen noord/oost-meters. Wat de neus doet, raakt de baan niet.

        vorige_rssi = None
        beste_rssi = None       # sterkste punt van de hele nadering
        ooit_gestegen = False   # is de RSSI ooit echt gestegen?
        correctie_index = 0
        laatste_punt = None

        for stap in range(1, MAX_NADERINGSSTAPPEN + 1):
            if accu_op():
                naar_huis(f"Batterij {status.get('battery_percent')}%")
                return

            melden('naderen',
                   f'Fase 3/5 — naderen: stap {stap}, koers {bearing:.0f}°',
                   fase=3)

            start_lat = status.get('gps_lat', 0.0)
            start_lon = status.get('gps_lon', 0.0)
            dn, de = _noord_oost(bearing, NADERING_STAP_M)
            _cmd_positie_offset(get_mav, mavutil, dn, de, bearing)
            voltooid, afgebroken = approach._wacht_op_stap(
                status, start_lat, start_lon, NADERING_STAP_M)
            if afgebroken:
                afbreken_door_piloot(); return
            if not voltooid:
                print(f"[search] stap {stap} niet bevestigd binnen timeout — "
                      f"meet op de huidige positie")
            if not pattern._wacht(SETTLE_NA_STAP_S, status):
                afbreken_door_piloot(); return

            punt, afgebroken = _meet_punt(
                status, 'naderen', samples, f'stap {stap} koers {bearing:.0f}°')
            if afgebroken:
                afbreken_door_piloot(); return
            if punt is None:
                naar_huis('Geen packets meer tijdens naderen')
                return

            laatste_punt = punt
            if vorige_rssi is None:
                vorige_rssi = beste_rssi = punt['rssi']
                continue

            stijging = punt['rssi'] - vorige_rssi
            print(f"[search] stap {stap}: RSSI {vorige_rssi:.0f} -> "
                  f"{punt['rssi']:.0f} dBm ({stijging:+.1f} dB) op "
                  f"koers {bearing:.0f}°")
            vorige_rssi = punt['rssi']

            if stijging >= MIN_STIJGING_DB:
                # Koers klopt; een eventuele eerdere correctie was een omweg
                # die we niet hoeven terug te draaien.
                ooit_gestegen = True
                correctie_index = 0
                beste_rssi = max(beste_rssi, punt['rssi'])
                continue

            # Geen stijging — nu is de vraag: staan we op de TOP van de
            # heuvel, of zijn we er langs gelopen?
            #
            # Op de top: dit punt is nog steeds het sterkste van de hele
            # nadering. Verder klimmen kan niet, dus we zijn er; fase 4 mag
            # beginnen. Dit is de verzadigingsstop uit het ontwerp, maar
            # gemeten t.o.v. onze eigen beste meting in plaats van t.o.v.
            # een absolute dBm-grens (zie TOP_MARGE_DB).
            if punt['rssi'] >= beste_rssi - TOP_MARGE_DB:
                kop.append(f'fase 3: top bereikt bij RSSI {punt["rssi"]:.0f} dBm '
                           f'na {stap} stappen')
                break

            # Duidelijk zwakker dan ons beste punt: we lopen ernaast.
            if correctie_index >= MAX_CORRECTIES:
                # Beide correcties geprobeerd en nergens beter. Steeg de RSSI
                # onderweg wél, dan zijn we bij de bron en is doorgaan naar de
                # pass zinvoller dan opgeven; steeg hij nooit, dan liepen we
                # niet naar een echte bron (ontwerp §5) en gaan we naar huis.
                if not ooit_gestegen:
                    naar_huis('RSSI steeg nergens tijdens het naderen — '
                              'geen echte bron')
                    return
                kop.append(f'fase 3: correcties op na {stap} stappen, '
                           f'doorgaan met beste punt {beste_rssi:.0f} dBm')
                break

            beste_rssi = max(beste_rssi, punt['rssi'])
            correctie = GRADIENT_CORRECTIE_GRADEN[correctie_index]
            correctie_index += 1
            bearing = (bearing + correctie) % 360
            kop.append(f'fase 3: koers {correctie:+.0f}° gecorrigeerd na '
                       f'{stijging:+.1f} dB')
            melden('naderen',
                   f'Fase 3/5 — RSSI stijgt niet ({stijging:+.1f} dB), '
                   f'koers {correctie:+.0f}° bijgesteld', fase=3)
        else:
            kop.append(f'fase 3: maximum van {MAX_NADERINGSSTAPPEN} stappen bereikt')

        if laatste_punt is None:
            naar_huis('Nadering gaf geen meetpunt')
            return

        # ================================================
        # FASE 4 — OVERVLIEGEN
        # ================================================
        if accu_op():
            naar_huis(f"Batterij {status.get('battery_percent')}%")
            return

        pass_afstand = SATURATIE_AFSTAND_M + PASS_VOORBIJ_M
        melden('overvliegen',
               f'Fase 4/5 — overvliegen: {pass_afstand:.0f} m op '
               f'{PASS_SNELHEID_CMS / 100:.1f} m/s', fase=4)

        _cmd_set_param(get_mav, mavutil, 'WPNAV_SPEED', PASS_SNELHEID_CMS)
        snelheid_aangepast = True
        if not pattern._wacht(1.0, status):
            afbreken_door_piloot(); return

        pass_start_lat = status.get('gps_lat', 0.0)
        pass_start_lon = status.get('gps_lon', 0.0)
        dn, de = _noord_oost(bearing, pass_afstand)
        _cmd_positie_offset(get_mav, mavutil, dn, de, bearing)

        def doel_bereikt():
            return approach._horizontale_afstand(
                status.get('gps_lat', 0.0), status.get('gps_lon', 0.0),
                pass_start_lat, pass_start_lon) >= pass_afstand * 0.95

        # Ruime timeout: de pass MOET voltooien, want de instort komt
        # achteraf uit de log. Halverwege afbreken levert niets bruikbaars.
        pass_timeout = pass_afstand / (PASS_SNELHEID_CMS / 100.0) * 2.0 + 10.0
        pass_begin = len(samples)
        afgebroken, aantal_pass = _log_continu(
            status, pass_timeout, 'overvliegen', samples, klaar_fn=doel_bereikt)
        if afgebroken:
            afbreken_door_piloot(); return

        gevonden = _zoek_instort(samples[pass_begin:])
        kop.append(f'fase 4: {aantal_pass} packets tijdens de pass')
        if gevonden:
            kop.append(f'fase 4: {gevonden[3]}')

        # ================================================
        # FASE 5 — AFRONDEN
        # ================================================
        _cmd_set_param(get_mav, mavutil, 'WPNAV_SPEED', STANDAARD_SNELHEID_CMS)
        snelheid_aangepast = False

        if gevonden:
            melden('rtl', f'Fase 5/5 — beacon gevonden ({gevonden[2]}) — '
                          f'RTL naar opstijgpunt', fase=5)
        else:
            melden('rtl', 'Fase 5/5 — geen positie bepaald — '
                          'RTL naar opstijgpunt', fase=5)
        approach._cmd_rtl(get_mav)

    except Exception as e:
        melden('fout', f'Fout tijdens zoekvlucht: {e}', active=False)
        print(f"[search] FOUT: {e}")

    finally:
        # Wat er ook gebeurd is: snelheid terugzetten, wegschrijven wat we
        # hebben, en de positie loggen als we er een hebben.
        # WPNAV_SPEED terugzetten gebeurt óók als de piloot heeft overgenomen.
        # Dat is geen stuurcommando en botst dus niet met "de zender is
        # primair": het zet alleen een instelling terug. Zou hij op 0,5 m/s
        # blijven staan, dan vliegt ook de RTL van de piloot half zo snel, en
        # dat kost precies de accu die hij dan nodig heeft.
        if snelheid_aangepast:
            try:
                from pymavlink import mavutil as _mavutil
                _cmd_set_param(get_mav, _mavutil, 'WPNAV_SPEED',
                               STANDAARD_SNELHEID_CMS)
            except Exception as e:
                print(f"[search] WPNAV_SPEED terugzetten mislukt: {e}")

        if samples:
            _schrijf_csv(samples, stempel, hoogte, start_gps, kop)

        melding = ''
        if gevonden:
            lat, lon, betrouwbaarheid, uitleg = gevonden
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
