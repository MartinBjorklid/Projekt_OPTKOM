# Polarisatortest: min/max för olika amplituder och frekvenser

`polarisator_test.py` driver LC-cellen på `Dev1/ao0` och mäter enbart
fotodiodens signal efter TIA på `Dev1/ai0`. Kräver `numpy`, `nidaqmx` och
NI-DAQmx; diagram kräver även `matplotlib`. Kör inte annan DAQ-kod samtidigt.

## Välj test

Kör `python polarisator_test.py` för att välja egna värden eller standardsvepet
i terminalen. I läget egna värden kan du ange en eller flera amplituder och
frekvenser, separerade med blanksteg. Alla kombinationer mäts.

Du kan också starta direkt med argument:

```sh
# Standardsvep: 102 kombinationer
python polarisator_test.py --mode sweep

# En egen kombination
python polarisator_test.py --amplitudes 4.5 --frequencies 75

# Flera egna kombinationer, med fyrkantvåg
python polarisator_test.py --amplitudes 2 4 6 --frequencies 20 100 300 --waveform square

# Längre insvängning och mätning; spara även råsignaler
python polarisator_test.py --mode sweep --settle 3 --duration 5 --save-raw
```

Standardsvepet mäter amplituderna **2, 3, 4, 5, 6, 7 V peak**, alltså **±2 till
±7 V** (4–14 V peak-to-peak), för varje frekvens:

**20, 30, 40, 50, 60, 70, 80, 90, 100, 150, 200, 250, 300, 350, 400, 450, 500 Hz.**

Sinus är standard. Amplitud får vara 0–10 V peak. AI använder normalt 5000
sampel/s. AO använder en separat klocka på högst denna takt och en hel,
symmetrisk period för att ge vald frekvens och undvika DC i LC-signalen.
Den faktiska AO-frekvensen och AI-takten hämtas från DAQ och sparas.
Vid 500 Hz blir det 8 AO-sampel/period och 10 AI-sampel/period med
standardinställningarna. AO-periodens längd är delbar med fyra så att även
sinus når exakt den valda positiva och negativa amplituden.

Varje punkt får normalt 1 s insvängningstid följt av 2 s mätning. AI läses
även under insvängningen för livevisning, men dessa sampel hålls utanför
de sparade mätvärdena. Mätfönstret får alltså inga sampel från insvängningen.
Standardsvepet tar drygt 5 minuter inklusive DAQ-omstarter. Anpassa `--settle`
till LC-cellens respons. Mättiden måste täcka minst två perioder.
`--sample-rate` får vara 100–5000 sampel/s och ska ge minst fyra sampel/period.

## Resultat

En tidsstämplad undermapp i `polarisator_resultat/` innehåller:

- `matningar.csv`: amplitud, begärd/faktisk frekvens, min/max, peak-to-peak,
  medelvärde, standardavvikelse, antal sampel och mätinställningar.
- `installningar.json`: körningens inställningar.
- `min_max.png`: min/max mot faktisk frekvens för varje amplitud.
- `alla_matvarden.png`: min, max, medelvärde, peak-to-peak, standardavvikelse
  och mörkerkorrigerade min/max för samtliga kombinationer.
- `tidskurvor/`: en PNG per kombination med hela fotodiodsignalen, en
  förstoring av de första fem LC-perioderna och den beräknade AO-vågformen.
- `.npz`-filer med råsignaler om `--save-raw` används.

Plottning är på som standard i båda körlägena. Under både insvängningen och
hårdvarumätningen visas fotodiodsignalen, aktuell amplitud/frekvens, min/max
och medelvärde i ett fönster som uppdateras ungefär var 0,1 s. Samma fönster
återanvänds genom hela svepet och varje komplett tidskurva sparas separat,
även utan `--save-raw`. Efter körningen visas jämförelserna för alla färdiga
mätpunkter. Diagrammen omfattar även egna värden och avbrutna svep.
AO-kurvan är beräknad från inställningarna på sin egen tidsaxel; den är inte
en uppmätt utgångssignal och anger inte fasläget relativt AI.

Min/max är **lägsta och högsta uppmätta AI-spänning under mätfönstret**.
De påverkas av brus, samplingsfrekvens och mättid. De är inte automatiskt
ljusnivåerna för parallella respektive korsade polarisatorer. Håll den optiska
uppställningen oförändrad under ett svep; kör separata svep för olika vinklar.
Råvärden behåller TIA-signalens tecken. `--dark-level` kan ange en uppmätt
mörkernivå; separata CSV-kolumner visar min/max minus denna nivå.

Varje färdig rad sparas omedelbart. Ctrl+C avbryter och behåller redan sparade
punkter. AO återställs till 0 V efter varje hårdvarumätning, även vid fel eller
Ctrl+C. Ett återställningsfel rapporteras.

`--ai` och `--ao` ändrar kanalerna, `--output` väljer resultatmapp och
`--no-plot` hoppar över diagrammet. Programkontroll utan DAQ:

```sh
python polarisator_test.py --mode sweep --simulate --no-plot
```

Simuleringen använder syntetiska data och är inte en modell av LC-cellen.
Simulerade resultat markeras både i CSV och mappnamnet.

Automatiska programtester utan DAQ: `python -m unittest discover -s tests -v`.
