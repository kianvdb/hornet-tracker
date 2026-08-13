# Tests voor search.py

Draaien vanuit de repo-hoofdmap of vanuit `tests/`:

```
python3 tests/test_eenheden.py     # rekenkern, ~1 s
python3 tests/test_vlucht.py       # vier volledige vluchten in simulatie, ~3 min
```

Beide eindigen met exitcode 0 bij succes. Ze schrijven naar `tests/uitvoer/`
en **raken `data/` nooit aan** — daar staat echte vluchtdata. `test_vlucht.py`
controleert dat expliciet aan het einde.

## Waarom deze tests hier staan en niet in /tmp

`/tmp` op deze Pi is een **tmpfs**: een RAM-schijf. Alles erin verdwijnt bij
elke herstart, en deze Pi gaat vaak uit — op 2 augustus alleen al acht keer
(veldsessies, accuwissels, de Afsluiten-knop). Testwerk in /tmp overleeft dat
niet.

## Wat er getest wordt, en waarom

Elke test hoort bij een fout die in het veld is opgetreden. De simulator
(`sim.py`) gebruikt een signaalmodel dat op de zes meetvluchten is geijkt.

| Test | Veldvlucht die hem opleverde |
|---|---|
| kortste draairichting | alle drie: eerste peilstap draaide bijna 360° de verkeerde kant op en werd midden in de draai gemeten |
| zwaartepunt boven de piek | 1-8: piek zat er 76° en 84° naast, zwaartepunt 1,8° en 5,7° |
| echte velddata 19:12 | zwaartepunt 11,5° tegen piek 18,9° van de beacon |
| lobdiagnose | 150-212° brede lobben met 1-3 dB variatie |
| terugkruising middelt de vertraging weg | enkele kruising ~2,4 m fout, middelpunt ~0,2 m |
| hovertest | 2-8 21:26: val na de richtingbepaling; motoren stonden al 103 PWM scheef |
| clipping als oplopende teller | fout in mijn eigen eerste testopzet |
| afgekeurde hovertest schrijft toch een CSV | anders zijn juist die meetwaarden weg |
| piloot-overname | zender blijft primair; geen RTL erna |

## Wat de simulatie NIET bewijst

- of ArduCopter `LOCAL_OFFSET_NED` met een yaw-veld doet wat wij denken
- de echte timing tussen de LoRa- en de MAVLink-thread
- hoe de signaal-instort er bij een echte gladde pass uitziet
- of de hovertest-drempels kloppen: die zijn **niet** op gezonde vluchten
  gekalibreerd, want die .BIN-logs zijn niet meer beschikbaar

Daar is hardware voor nodig. De simulatie vangt regressies en rekenfouten,
niet de fysica.
