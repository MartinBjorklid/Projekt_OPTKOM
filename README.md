# Projekt_OPTKOM
Projekt i optisk kommunikation HT2026

## Valbara 2, 4 och 8 nivåer

`program.py`, `felsok_kommunikation.py`, `skicka.py` och `taemot.py` stöder
`--levels 2|4|8` och `--step-time SEKUNDER`. Även `--step_time` och
`--step time SEKUNDER` accepteras. Standard är 2 nivåer och 0,004 s per symbol.
En symbol innehåller 1, 2 respektive 3 nyttobitar.

Ange mottagarens gränser med `--threshold` följt av **1, 3 eller 7**
strikt stigande voltvärden. Under första gränsen avläses nivå 0; vid eller
över en gräns väljs nästa nivå. För 4/8 nivåer krävs explicit angivna gränser.
För 2 nivåer är standarden 2,3 V i program/mottagare och 3,0 V i felsökning,
som tidigare. Ange samma tröskel explicit för att jämföra körningarna.

Exempel (trösklarna är bara exempel, ersätt med era uppmätta AI0-gränser):

```sh
python program.py --levels 4 --threshold 1.0 2.0 3.0 --step-time 0.004
python felsok_kommunikation.py --levels 4 --threshold 1.0 2.0 3.0 --step-time 0.004
python program.py --levels 8 --threshold 0.5 1.0 1.5 2.0 2.5 3.0 3.5 --step-time 0.008
python felsok_kommunikation.py --levels 8 --threshold 0.5 1.0 1.5 2.0 2.5 3.0 3.5 --step-time 0.008
python program.py --levels 2 --threshold 2.3 --step time 0.004
```

I felsökningens serietest används tiden från kommandoraden om den anges.
Annars frågar programmet efter flera symboltider, som tidigare. Valda nivåer
används både med och utan Hamming. Diagram och CSV visar symboler, medan
mottagen nyttodata fortfarande består av bitar för Huffman/Hamming.

Sändarens amplituder är jämnt fördelade mellan 0 och 5 V, med växlande
polaritet mellan symbolerna precis som tidigare. Exempelvis används
0, 1,667, 3,333 och 5 V i 4-nivåläget. **Mottagarens trösklar avser AI0,
inte dessa AO-amplituder.** Mät varje nivås TIA-spänning och välj gränser
mellan de uppmätta nivåerna. Avkodaren förutsätter stigande, åtskilda
AI0-nivåer; fler nivåer behöver verifieras på den verkliga optiska länken.
Minsta tillåtna symboltid för ordinarie program är 0,00025 s.

### Ramformat och API

2-nivåläget behåller befintligt start-/slutformat; data som skulle ge ett
förtida slutord avvisas. 4/8-nivålägena använder startsekvensen på yttersta
nivåerna, följd av 16 bitars nyttobitlängd (MSB först) och dess 16 bitars
invers, även dessa på yttersta nivåerna. Därefter följer packade nyttodata
(2 eller 3 bitar per symbol), eventuell nollutfyllnad, och slutsekvensen på
yttersta nivåerna. Längden tar bort utfyllnaden exakt och gör att slutord
får förekomma i nyttodatan. Fel i längdkontrollen eller slutordet avvisas.
Maximal nyttodata är 4096 bitar. Båda ändar måste använda samma nivåantal
och symboltid; de förhandlas inte automatiskt. Huffman/Hamming arbetar
fortsatt på bitar. Längdkontrollen är ingen CRC för nyttodatan.

- `send_payload(bits, step_time=..., levels=...)`: ramar in och skickar bitar.
- `send_symbols(symbols, step_time=..., levels=...)`: skickar råa symboler.
- `send_binary_list(...)`: befintligt binärt rågränssnitt behålls.
- `receive_continuous(..., levels=..., threshold=[...])`: returnerar nyttobitar.

`skicka.py` fristående använder Huffman utan Hamming; `program.py` använder
båda. `taemot.py` fristående skriver ut bitarna. Arkivfiler i `old/` är historiska.

### Tester utan hårdvara

```sh
python -m unittest discover -s tests -v
python hastighetstest.py --simulate --levels 4 --threshold 0.667 2 3.333 --bit-ms 4 --repeats 2
python hastighetstest.py --simulate --levels 8 --threshold 0.286 0.857 1.429 2 2.571 3.143 3.714 --bit-ms 4 --repeats 2
```

Simuleringen använder idealiserade AI-nivåer mellan 0 och 4 V med brus.
Den bevisar digital återavkodning, inte att LC/TIA-hårdvaran kan skilja nivåerna.
Pythonpaketen `numpy`, `matplotlib` och, för hårdvarukörningar, `nidaqmx`
samt NI-DAQmx-drivrutinen behövs.
