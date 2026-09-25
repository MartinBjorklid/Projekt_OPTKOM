# Fristående tröskelkalibrering

`kalibrering.py` skickar ett känt testmönster på AO0 och mäter TIA-signalen på
AI0. Den föreslår en tröskel i volt men ändrar inga programinställningar eller
mottagarfiler. Ingen automatisk inkoppling vid dagens första körning har gjorts;
kör verktyget manuellt när ni vill bedöma dagens signal.

## Körning på DAQ-datorn

Kräver Python 3, numpy, nidaqmx och fungerande NI-DAQmx-drivrutin.
Stäng andra överföringar/gränssnitt som använder samma DAQ. Verktyget driver
utgången med samma 0/±5 V-modulation som projektets sändare och återställer AO
till 0 V i slutet, även efter fel. Ett återställningsfel rapporteras.

```sh
python kalibrering.py
```

Anpassa kanaler, bittid och jämförelsetröskel vid behov:

```sh
python kalibrering.py --ai Dev1/ai0 --ao Dev1/ao0 --step-time 0.004 --current-threshold 2.3
```

Samplingsfrekvensen följer mottagarens beräkning om `--sample-rate` utelämnas.
`--min-margin 0.1` är ett justerbart krav på minst 0,1 V mellan nollornas
95-percentil och den lägsta 5-percentilen för de två ett-polariteterna.
Det är ett startvärde, inte en hårdvaruvaliderad gräns.

## Vad resultatet betyder

Träningsdelen innehåller platåer och blandade bitar. Startläge/bitfas söks mot
känt facit utan att använda den befintliga tröskeln. Därefter mäts medianen i
varje bits mittersta tredjedel. Båda AO-polariteterna kontrolleras separat.
Tröskeln placeras mitt i intervallet mellan nivåernas brusgränser.

256 efterföljande testbitar används för validering med samma tidsläge och
tröskel; dessa bitar används inte för att optimera tröskeln eller tidsläget.
Alla måste bli rätt för ett godkänt förslag. Underkänd mätning ger ingen
rekommenderad tröskel. Kandidaten, om nivåerna räcker, finns ändå i rapporten.

Verktyget sparar en ny undermapp under `kalibreringsresultat/` per körning:

- `rapport.json`: datum, kanaler, verklig samplingsfrekvens/bittid, nivåer,
  kandidat, rekommendation, felantal och orsaker till underkännande.
- `matdata.npz`: rå AI-signal, skickade bitar och tidsinställningar för analys.

Inga tidigare resultat skrivs över. `--output SÖKVÄG` väljer resultatmapp.
Returkod 0 betyder godkänt förslag, 2 underkänd mätning och 1 körfel.

Ett godkänt resultat verifierar nivåseparation och kända testbitar. Det bevisar
inte att den vanliga mottagarens startsekvens hittas eller att långa ramar
fungerar. Tidsökningen använder känt facit och kan därför lyckas när vanlig
startsynk misslyckas. Kontrollera verklig meddelandemottagning efter ett
eventuellt manuellt tröskelbyte. Ingen ändring görs automatiskt.

Om tidsläget hamnar vid sökfönstrets kant, prova större `--max-delay` (standard
0,5 s). Vid överlappande nivåer, polaritetsberoende problem eller klippning
behöver den optiska/elektriska signalen undersökas innan tröskeln byts.

## Test utan hårdvara

```sh
python kalibrering.py --simulate --output /tmp/kalibrering-demo
python -m unittest test_kalibrering -v
```

Simulerade rapporter märks tydligt och är inte en kalibrering av er utrustning.
