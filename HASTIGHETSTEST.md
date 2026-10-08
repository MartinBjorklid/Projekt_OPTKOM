# Amplitud- och hastighetstest för optisk kommunikation

`hastighetstest.py` jämför den verkliga binära sändsignalens amplitud och bittid. Nollor ger 0 V och ettor ger ±A med polaritet enligt bitpositionen, precis som i `skicka.py`. Detta är inte ett sinussvep. Kör på datorn med NI USB-6003, Python, numpy, nidaqmx och NI-DAQmx. För diagram behövs matplotlib. Stäng andra program som använder samma DAQ-kanaler.

## Kör standardtestet

```sh
python hastighetstest.py --distance-m 0.4 --polarizer-deg 195 0
```

Standardtestet omfattar:

- Amplituder **±3, ±4 och ±5 V**, med 0 V för nollor.
- Bittider **32, 16, 10, 6 och 4 ms**, långsammast först. Det motsvarar **31,25; 62,5; 100; 166,7 och 250 bit/s** på den binära kanalen.
- Sex mönster: `0101`, `1010`, 16-bitarsblock av nollor/ettor, isolerade ettor, isolerade nollor och reproducerbar slumpdata. Isolerade bitar har 17 bitars mellanrum, så båda ett-polariteterna förekommer.
- **256 nyttobitar per paket, tre repetitioner per mönster**, totalt 270 screeningpaket om alla kalibreringar godkänns.
- Separat tröskelkalibrering för varje amplitud/bittid.
- Efter screening: ny kalibrering och minst **10 000 nya testbitar för den snabbaste godkända bittiden vid varje amplitud**. Standard ger 10 752 bitar, fördelade på alla sex mönster, per kandidat.
- Textprov med `hej`, `test` och `optisk kommunikation` för godkända kandidater, om projektets `Signalbehandling.py` finns bredvid testfilen. Detta använder projektets Huffman/Hamming-kedja. Gränssnitt, trådar och hela `program.py` körs inte av testet.
- Sparade råsignaler och ögondiagram som standard. Fotodioden mäts på AI0 med 20 000 sampel/s, AO0 driver cellen.

Räkna med ungefär **25–45 minuter plus DAQ- och filsparande**, beroende på vilka hastigheter som klarar screening. Programmet skriver sin uppskattning och framsteg. Underkända kalibreringar kan förkorta körningen betydligt. Rådata kan ta hundratals MB; `--no-save-raw` stänger av dem. `--no-plot` stänger av diagram.

Amplitudernas ordning roteras mellan försök för att minska systematisk påverkan av drift. Samma skickade testdata används för alla amplituder och hastigheter i screening. Bekräftelsen använder ett separat slumpfrö. Vinklar, avstånd och ljusförhållanden är manuellt angivna mätuppgifter; programmet flyttar ingen optik.

## Tröskel och verklig mottagning

Kalibreringen använder 384 träningsbitar med långa platåer och blandade bitar, följt av 256 separata valideringsbitar. Tid och tröskel anpassas endast på träningen. Tröskeln placeras mellan nollornas 95-percentil och den lägsta 5-percentilen för positiva/negativa ettor. Båda polaritetsgrupperna måste ha minst 30 träningsbitar. Standard kräver minst 0,1 V separation; detta är en justerbar experimentgräns, inte en verifierad LC-specifikation.

Efter kalibreringen låses tröskeln under inställningens datapaket. Dataavkodningen söker startsekvensen utan facit och använder medianen i bitens mittersta tredjedel, enligt `taemot.py`. Skickat facit används först efter avkodningen för felräkning och nivådiagnostik. En underkänd kalibrering redovisas tydligt och får inte bli en felfri kandidat med noll testpaket.

```sh
# Tätare amplitudjämförelse och fler screeningpaket
python hastighetstest.py --amplitudes 2.5 3 3.5 4 5 --repeats 10 --distance-m 0.4

# Granska samma fasta tröskel som vanliga mottagaren använder
python hastighetstest.py --threshold 2.3 --distance-m 0.4

# Ett kortare inledande test, utan lång bekräftelse
python hastighetstest.py --bit-ms 16 4 --repeats 1 --confirm-bits 0 --distance-m 0.4

# Längre bekräftelse
python hastighetstest.py --confirm-bits 100000 --distance-m 0.4
```

`--threshold` väljer en fast tröskel som också måste klara kalibreringens oberoende validering. Inga trösklar eller amplituder skrivs in i den vanliga sändaren/mottagaren automatiskt.

## Övergångstider och uppmätt drivspänning

För nivåseparation och bitfel räcker AI0. För svarstid relativt verklig drivning kan ni ansluta en andra AI-kanal till den faktiska LC-drivspänningen enligt er NI-koppling och sedan ange exempelvis:

```sh
python hastighetstest.py --ao-monitor-ai Dev1/ai1 --distance-m 0.4 --polarizer-deg 195 0
```

Kanalen ska mäta LC-spänningen, inte en komparatorutgång. Testet använder RSE på båda ingångarna och ±10 V mätområde. Kontrollera att den aktuella LC-kopplingens referens passar RSE innan den kopplas in. AI-kanalerna delar högst 100 kS/s sammanlagt. Standard 20 kS/s per kanal ryms inom gränsen. NI-kortet multiplexar ingångarna; de är inte exakt samtidiga.

Kalibreringsrapporten visar uppmätt 10–90 % stigtid, 90–10 % falltid och fördröjningen till den första 10-procentsändringen, om tydliga övergångar finns. Tiderna gäller mätkedjans respons på långa träningsblock, inte en isolerad LC-cell. En utebliven gränspassage redovisas som tom. Utan uppmätt AO-referens lämnas svarstiderna otillgängliga; en beräknad drivkurva används inte som fasreferens.

## Avstånd och mättnadskontroller

Flytta uppställningen till 2 meter och kör en ny mätning:

```sh
python hastighetstest.py --distance-m 2 --polarizer-deg 195 0
```

Mörker/överhörning och mättnad undersöks i separata, märkta körningar. Blockera lasern eller ändra ljusförhållandena fysiskt först. `--condition` är endast en etikett; den styr inte lasern. Kontroller ger ingen kommunikationsrekommendation.

```sh
python hastighetstest.py --amplitudes 3 5 --bit-ms 16 4 --condition blocked-laser --confirm-bits 0 --distance-m 0.4
python hastighetstest.py --condition reduced-light --distance-m 0.4 --notes "Reducerad ljuseffekt på fotodioden"
```

`constant-light` kan användas för en uppställning där fotodioden får konstant ljus trots LC-drivning. Ange gärna LC-vinkeln med `--lc-angle-deg` och övriga uppgifter med `--notes`. Utan `--distance-m` är avståndet okänt i rapporten.

## Resultat och beslut

Varje körning får en ny tidsstämplad mapp under `testresultat/`:

- `installningar.json`: inställningar, mätuppställning, signalform och simulationsflagga.
- `forsok.csv`: ett datapaket per rad, fas, amplitud, faktisk bittid, tröskel, bitfel, BER, paketfel, nivågränser och mät-/ramtid.
- `sammanfattning.csv`: jämförelse per amplitud/bittid, separat för screening och bekräftelse.
- `monster_sammanfattning.csv`: samma jämförelse uppdelad på bitmönster.
- `*_kalibrering.json`: nivåer, båda polariteterna, tröskel, valideringsfel, eventuell svarstid och orsaker till underkännande.
- `rapport.json`: genomförandestatus, resultat, begränsningar och bästa uppmätta kandidat.
- `textforsok.csv`: meddelanderesultat och bitfel före Hamming när textprovet kan köras.
- `rasignaler/*.npz`: signal, skickade bitar inklusive ram, faktisk AI-takt/bittid, amplitud, simulationsflagga och eventuell uppmätt AO. Paketfiler innehåller även mottagarens startindex och avkodade bitar; kalibreringar innehåller träningsgränsen.
- `ogondiagram/`: ett diagram per inställning/mönster/fas när mottagaren hittar en startsekvens. Grön markering visar mitten-tredjedelen, röd linje visar låst tröskel.
- `sammanfattning.png`: BER, paketfelsandel och korrekt nyttobitshastighet mot kanalens bithastighet. Om matplotlib saknas sparas tabeller/rådata ändå.

**Läs BER tillsammans med paketfelsandelen och antalet jämförda bitar.** BER beräknas bara för ramar med rätt längd. Missad start, saknat slutord och fel längd räknas som paketfel med tom BER, aldrig som noll bitfel. Statistik från ett enda växlande mönster kan sakna ena ett-polariteten; det redovisas som tomt i stället för påhittat.

En kandidat måste ha godkänd kalibrering, samtliga begärda paket/mönster genomförda och inga observerade paketfel eller ADC-klippningar. Bästa kandidaten väljs efter korrekt nyttodata per uppmätt överföringstid. Om bekräftelsen är på används bara godkända bekräftelser. Om textprovet körts måste även alla begärda meddelanden stämma. Saknad `Signalbehandling.py` eller avstängt textprov redovisas som en begränsning. Slutord i kodad text ger ett särskilt protokollfel, inte ett optiskt bitfel.

Nyttobitar i huvudtestet är råa testbitar före Hamming. `korrekta_nyttobitar_per_ram_s` inkluderar start/slutram men inte vilotider. `korrekta_nyttobitar_per_s` inkluderar paketets DAQ-uppstart och inspelning/vilotider men exkluderar kalibrering, analys, diagram, filsparande och separat nollställning. `session_elapsed_s` redovisar den samlade experimenttiden. Textprovet redovisar korrekt komprimerad nyttodata per sekund efter hela kodningskedjan. Dessa mått ska inte förväxlas med nominell bitklocka eller okomprimerade tecken per sekund.

Noll observerade fel i 10 000 bitar bevisar inte felfri kommunikation. Bekräfta slutligen inställningen i det vanliga programmet, på minst 2 meter och med realistiska ljusförhållanden. ADC-flaggan gäller ±10 V och utesluter inte att TIA:n mättas kring 4,7 V.

## Avbrott och kända begränsningar

Färdiga paket skrivs löpande. Ctrl+C sparar en ofullständig rapport utan rekommendation. Hårdvarufel avbryter körningen och redovisas separat från optiska paketfel. AO återställs till 0 V efter varje hårdvaruinspelning, även vid undantag och avbrott. Ett återställningsfel rapporteras.

Er befintliga polaritet efter bitindex kan ge DC för vissa bitmönster, exempelvis 0101. Testet behåller den signalformen för att jämförelsen ska motsvara nuvarande sändning. `ao_mean_V` visar ramens beräknade medelspänning. En framtida balanserad symbolsignal behöver jämföras som ett separat modulationsalternativ.

Returkoder: 0 = genomförd körning med kandidat; 2 = genomförd körning utan kandidat (även kontrollmätning); 1 = körfel; 130 = avbrutet. Simulerade kandidater är aldrig hårdvaruverifierade.

## Verifiering utan hårdvara

```sh
python hastighetstest.py --simulate --no-plot --no-save-raw
python -m unittest test_hastighetstest test_kalibrering -v
```

Simulerade mappar märks `SIMULERING`, rapporter och NPZ-filer märks uttryckligen. Simuleringen testar arbetsflödet, inte vilken amplitud som är bäst för er LC-cell.
