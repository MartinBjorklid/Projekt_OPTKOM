# Automatiskt hastighetstest

Kör på datorn som är ansluten till NI USB-6003. Python, numpy, nidaqmx och NI-DAQmx-drivrutinen behövs. Filen är fristående; originalfilerna behöver inte ändras. Stäng andra program som använder DAQ-kanalerna.

```sh
python hastighetstest.py
```

Standardtestet kör 8, 6, 4, 3 och 2 ms per bit. Varje hastighet får 10 paket av vardera tre mönster: växlande bitar, 16-bitarsblock av nollor/ettor och reproducerbar slumpdata. Varje paket har 256 nyttobitar. Standard är AO0, AI0 (RSE) och tröskel 2,3 V. Räkna med cirka fem minuter plus uppstart och analys.

```sh
python hastighetstest.py --bit-ms 4 3 2 --repeats 30 --threshold 2.3 --save-raw
```

Testet använder befintlig ±5/0 V-signalform med polaritet från bitpositionen. Det korrigerar INTE den tidigare identifierade obalansen för vissa bitmönster. Bekräfta att signalformen är lämplig för er LC-kristall före fysisk körning; kristallens tillåtna drivning har inte verifierats. AO återställs till 0 V efter varje försök, även vid undantag. Fel vid återställning visas och avbryter körningen.

## Resultat

Varje körning får en egen mapp under testresultat:

- forsok.csv: ett försök per rad, bitfel, BER, paketfel, mottagen längd, ramtid och total mätt överföringstid.
- sammanfattning.csv: resultat per faktisk bittid och korrekt mottagna nyttobitar per sekund.
- installningar.json: inställningar och slumpfrö.
- Med --save-raw: komprimerade NPZ-filer med analog signal, samplingsfrekvens, faktisk bittid och sända nyttobitar.

BER beräknas endast för ramar med rätt mottagen längd. Synkfel, utebliven slutsekvens och fel längd räknas som paketfel, men får tom BER – de behandlas aldrig som noll bitfel. Läs därför ALLTID BER tillsammans med paketfelsandelen och antalet jämförda bitar. Ett paket är korrekt endast om alla nyttobitar stämmer.

Avkodaren söker startsekvensen och avläser medianen i varje bits mittersta tredjedel, som principen i taemot_synk.py. Nyttodatans facit används först efter avkodning. Detta är en separat mätmottagare; testet kör inte hela program.py, Huffman eller Hamming och mäter därför råa nyttobitar, inte tecken per sekund. Testramar väljs utan förtida slutord, inklusive vid gränsen till slutsekvensen. Slumpmönstret är därför villkorat, inte helt obundet.

Tiden inkluderar DAQ-uppstart, inspelning, sändning och viloperioder men exkluderar efterföljande analys, filsparande och separat nollställning. Ingen grafpaus och ingen omsändning ingår. Ramtiden redovisas separat. Datafiler skrivs löpande så att avslutade försök bevaras vid avbrott. Hårdvarufel avbryter körningen i stället för att räknas som optiska paketfel.

Förslaget i slutet väljer högst uppmätt nyttobitshastighet bland inställningarna utan observerade paketfel. Noll fel i ett kort test bevisar inte felfri kommunikation. Bekräfta kandidaten med fler repetitioner och realistiska ljusförhållanden; jämför även långsammare inställning för marginal.

## Kontroll utan utrustning

```sh
python hastighetstest.py --simulate --repeats 1
```

Simuleringen använder idealiska signalnivåer med lite brus och markerar resultatmappen SIMULERING. Den verifierar testflödet, inte LC-kristallens prestanda. Dess tid är syntetisk.
