"""Felsök optisk kommunikation med riktiga NI-DAQmx.

Lägg filen bredvid skicka.py, taemot.py och Signalbehandling.py.
Kör: python felsok_kommunikation.py

Felsökningsprogrammet använder er befintliga skicka.send(), men läser AI0
separat för att spara *alla* analoga sampel. Kör inte taemot.py samtidigt:
en AI-kanal kan inte ägas av två pågående DAQ-uppgifter samtidigt.
"""

import csv
import threading
import time

import matplotlib.pyplot as plt
import numpy as np

from skicka import Start_seq, Slut_seq, send, step_time
from Signalbehandling import codes, huffman_encode, frame_symbols, communication_args


# Samma bittid och tröskel som er nuvarande program.py / taemot.py.
BITTID = step_time              # s per symbol
AI_FREKVENS = 6250        # Hz; beräknas från symboltid i main
TROSKEL = [3.0]           # Stigande gränser mellan mottagna nivåer
NIVAER = 2
CLI_BITTID = None
AI_KANAL = "Dev1/ai0"     # INTE komparatorns AI1
START_FORSENING = 0.5     # s: samla baslinje före sändning
EFTERTID = 0.3            # s: samla data efter sändning


class Inspelning(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.klar_att_mata = threading.Event()
        self.stoppa = threading.Event()
        self.block = []
        self.fel = None

    def run(self):
        try:
            import nidaqmx
            from nidaqmx.constants import AcquisitionType, TerminalConfiguration
            with nidaqmx.Task() as task:
                task.ai_channels.add_ai_voltage_chan(
                    AI_KANAL,
                    terminal_config=TerminalConfiguration.RSE,
                    min_val=-10.0,
                    max_val=10.0,
                )
                task.timing.cfg_samp_clk_timing(
                    rate=AI_FREKVENS,
                    sample_mode=AcquisitionType.CONTINUOUS,
                    samps_per_chan=int(AI_FREKVENS * 2),
                )
                task.in_stream.input_buf_size = int(AI_FREKVENS * 10)
                task.start()
                self.klar_att_mata.set()

                # Läs regelbundet: vi ska inte hoppa över övergångar.
                while not self.stoppa.is_set():
                    data = task.read(
                        number_of_samples_per_channel=max(1, int(AI_FREKVENS // 10)),
                        timeout=2.0,
                    )
                    self.block.append(np.asarray(data, dtype=float))

        except Exception as exc:
            self.fel = exc
            self.klar_att_mata.set()


def fast_blockavkodning(signal, sampel_per_bit):
    """Fasta symbolblock utan faslåsning.

    Här startar blocken vid felsökningsinspelningens början. Detta är inte en
    exakt återspelning av en separat körning av taemot.py, som startar sin egen
    DAQ-klocka och därmed kan få *annan* blockfas.
    """
    antal = len(signal) // sampel_per_bit
    medel = signal[:antal * sampel_per_bit].reshape(
        antal, sampel_per_bit
    ).mean(axis=1)

    # Återanvänd ramavkodaren med konstanta blockmedelvärden.
    from taemot import SynkadBitavkodare
    avkodare = SynkadBitavkodare(30, TROSKEL, levels=NIVAER)
    try:
        avkodare.mata_in(np.repeat(medel, 30))
    except (ValueError, RuntimeError) as exc:
        print(f"Ramfel vid fast blockfas: {exc}")
    start = None if avkodare.start_index is None else avkodare.start_index // 30
    slut = avkodare.nasta_bit if avkodare.klar else None
    return start, slut, avkodare.payload if avkodare.klar else None, medel


def hitta_basta_bitfas(signal, facit, sampel_per_bit):
    """Diagnostisk jämförelse MOT KÄNT FACIT, inte en verklig avkodare.

    Testar tidpunkter och väljer den där flest bitar motsvarar sända bitar.
    På så sätt går det att skilja dålig analog signal från fel blockfas.
    """
    antal_bitar = len(facit)
    sista_start = len(signal) - antal_bitar * sampel_per_bit
    if sista_start < 0:
        raise ValueError("För kort inspelning för att rymma hela meddelandet.")

    # Medelvärde nära bitens MITT, så att långsamma flanker stör mindre.
    halvt_fonster = max(1, sampel_per_bit // 5)
    filterkarn = np.ones(2 * halvt_fonster) / (2 * halvt_fonster)
    filtrerat = np.convolve(signal, filterkarn, mode="same")
    mittpunkter = ((np.arange(antal_bitar) + 0.5) * sampel_per_bit).astype(int)

    steg = max(1, sampel_per_bit // 10)
    kandidater = np.arange(0, sista_start + 1, steg, dtype=int)
    if sista_start not in kandidater:
        kandidater = np.append(kandidater, sista_start)

    bast = None
    for batchstart in range(0, len(kandidater), 128):
        starter = kandidater[batchstart:batchstart + 128]
        v = filtrerat[starter[:, None] + mittpunkter[None, :]]
        bitar = np.searchsorted(TROSKEL, v, side="right")
        antal_ratt = (bitar == facit[None, :]).sum(axis=1)
        pos = int(np.argmax(antal_ratt))
        resultat = (int(antal_ratt[pos]), int(starter[pos]))
        if bast is None or resultat[0] > bast[0]:
            bast = resultat

    _, start_sample = bast
    centrum_index = start_sample + mittpunkter
    volt = filtrerat[centrum_index]
    bitar = np.searchsorted(TROSKEL, volt, side="right")
    return start_sample, centrum_index, volt, bitar


def spara_och_rita(signal, facit, nyttobitar):
    sampel_per_bit = int(round(AI_FREKVENS * BITTID))
    t = np.arange(len(signal)) / AI_FREKVENS

    with open("felsok_ai0.csv", "w", newline="", encoding="utf-8") as fil:
        writer = csv.writer(fil)
        writer.writerow(["tid_s", "ai0_V"])
        writer.writerows(zip(t, signal))
    print("Sparade alla analoga sampel i felsok_ai0.csv")

    block_start, block_slut, block_payload, _ = fast_blockavkodning(
        signal, sampel_per_bit
    )
    print("\n--- Fasta symbolblock (utan faslåsning) ---")
    if block_start is None:
        print("Hittade INTE startsekvensen med dessa blockgränser.")
    elif block_slut is None:
        print(f"Start hittad vid block {block_start}, men INGEN slutsekvens.")
    else:
        facit_payload = np.asarray(nyttobitar)
        print(f"Start i block {block_start}, slut i block {block_slut}.")
        print(f"Mottaget antal nyttobitar: {len(block_payload)}; förväntat: {len(facit_payload)}")
        print(f"Nyttobitarna överensstämmer: {np.array_equal(block_payload, facit_payload)}")

    start_sample, centrum_index, volt, bitar = hitta_basta_bitfas(
        signal, facit, sampel_per_bit
    )
    fel_index = np.flatnonzero(bitar != facit)


    print("\n--- Symbolfas optimerad MOT det kända sända meddelandet ---")
    print(f"Uppskattad start: {start_sample / AI_FREKVENS:.3f} s efter inspelningsstart")
    print(f"Fel vid tröskel {TROSKEL} V: {len(fel_index)} av {len(facit)} symboler")
    for level in range(NIVAER):
        values = volt[facit == level]
        if len(values):
            print(f"Nivå {level}: median AI0 {np.median(values):.3f} V, "
                  f"intervall {values.min():.3f} ... {values.max():.3f} V")
    if len(fel_index):
        print("Första felaktiga symbolpositionerna (0-baserade):", fel_index[:25].tolist())
        print("Facit på dessa positioner:", facit[fel_index[:25]].tolist())
        print("Avläst på dessa positioner:", bitar[fel_index[:25]].tolist())

    with open("felsok_bitar.csv", "w", newline="", encoding="utf-8") as fil:
        writer = csv.writer(fil)
        writer.writerow(["tid_s", "sand_symbol", "avlast_symbol", "ai0_mitt_V", "fel"])
        for index, v, s, mott in zip(centrum_index, volt, facit, bitar):
            writer.writerow([index / AI_FREKVENS, int(s), int(mott), float(v), int(s != mott)])
    print("Sparade bitjämförelsen i felsok_bitar.csv")

    # Två diagram med samma tidsaxel: analog signal och digital tolkning.
    fig, (ax0, ax1) = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    ax0.plot(t, signal, linewidth=0.8, label="Uppmätt AI0")
    for threshold in TROSKEL:
        ax0.axhline(threshold, color="tab:red", linestyle="--", label=f"Tröskel {threshold:g} V")
    ax0.scatter(centrum_index / AI_FREKVENS, volt, s=12, color="black", label="Symbolers mittpunkter")
    if len(fel_index):
        ax0.scatter(centrum_index[fel_index] / AI_FREKVENS, volt[fel_index],
                    s=35, color="tab:red", marker="x", label="Feltolkade symboler")
    ax0.set_ylabel("TIA-utgång AI0 [V]")
    ax0.legend(loc="upper right")
    ax0.grid(True)

    bit_tid = (start_sample + np.arange(len(facit)) * sampel_per_bit) / AI_FREKVENS
    ax1.step(bit_tid, facit, where="post", label="Sänd symbol (facit)", linewidth=1.8)
    ax1.step(bit_tid, bitar + 0.05, where="post", label="AI0 tolkad symbol (+0,05 för synlighet)", alpha=0.75)
    ax1.set_ylim(-0.2, NIVAER - 1 + 0.35)
    ax1.set_xlabel("Tid från inspelningsstart [s]")
    ax1.set_ylabel("Symbol")
    ax1.legend(loc="upper right")
    ax1.grid(True)
    ax1.set_xlim(max(0, bit_tid[0] - 0.2), min(t[-1], bit_tid[-1] + BITTID + 0.2))
    fig.tight_layout()
    plt.show()


def enstaka_felsokning():
    text = input("Text att skicka och felsöka: ").lower()
    # Använd exakt samma kodtabell och ram som er befintliga sändare.
    nyttobitar = huffman_encode(text, codes)
    facit = np.asarray(frame_symbols(nyttobitar, NIVAER), dtype=int)
    if len(facit) * BITTID > 180:
        raise ValueError("Välj ett kortare meddelande till första felsökningstestet.")
    print(f"Sänder {len(facit)} symboler ({len(facit) * BITTID:.1f} s), AI0 {AI_FREKVENS} sampel/s")
    inspelning = Inspelning()
    inspelning.start()
    if not inspelning.klar_att_mata.wait(timeout=5.0):
        inspelning.stoppa.set()
        raise RuntimeError("AI0 hann inte starta inom fem sekunder.")
    if inspelning.fel is not None:
        raise RuntimeError(f"Kunde inte starta AI0: {inspelning.fel}")

    send_fel = None
    try:
        time.sleep(START_FORSENING)
        send(text, step_time=BITTID, levels=NIVAER)
    except Exception as exc:
        send_fel = exc
    finally:
        time.sleep(EFTERTID)
        inspelning.stoppa.set()
        inspelning.join(timeout=4.0)

    if inspelning.is_alive():
        raise RuntimeError("AI0-tråden gick inte att stoppa; kör inte om programmet förrän den avslutats.")
    if inspelning.fel is not None:
        raise RuntimeError(f"Problem under AI0-inspelningen: {inspelning.fel}")
    if send_fel is not None:
        raise RuntimeError(f"Sändningen misslyckades: {send_fel}") from send_fel
    if not inspelning.block:
        raise RuntimeError("Inga sampel mottogs från AI0.")
    signal = np.concatenate(inspelning.block)
    spara_och_rita(signal, facit, nyttobitar)


def las_positivt_tal(prompt, heltal=False):
    while True:
        try:
            value = int(input(prompt)) if heltal else float(input(prompt).replace(",", "."))
            if value > 0 and np.isfinite(value):
                return value
        except ValueError:
            pass
        print("Ange ett positivt heltal." if heltal else "Ange ett positivt tal.")


def kor_serietest(text, bittid, hamming):
    from skicka import send_symbols
    from taemot import receive_continuous
    from Signalbehandling import encode, decode, huffman_decode, tree

    komprimerade = huffman_encode(text, codes)
    if hamming:
        kodade, H, padding = encode(komprimerade)
        nyttobitar = kodade.tolist()
    else:
        nyttobitar = komprimerade
    ram = frame_symbols(nyttobitar, NIVAER)
    if len(nyttobitar) > 4096 or len(ram) * bittid > 180:
        raise ValueError("Meddelandet är för långt för serietestet.")
    if max(25, int(round(5000 * bittid))) / bittid > 100_000:
        raise ValueError("Bittiden är för kort för mottagarens samplingsfrekvens.")

    redo = threading.Event()
    mottagna = []
    mottagarfel = []
    timeout_s = max(15.0, len(ram) * bittid + 6)

    def lyssna():
        try:
            mottagna.extend(receive_continuous(
                step_time=bittid, channel=AI_KANAL, threshold=TROSKEL, levels=NIVAER,
                timeout_s=timeout_s, ready_event=redo))
        except Exception as exc:
            mottagarfel.append(str(exc))
            redo.set()

    trad = threading.Thread(target=lyssna)
    start = time.perf_counter()
    trad.start()
    fel = ""
    # Sändaren ritar ett diagram per anrop. Stäng bara de nya figurerna.
    tidigare_figurer = set(plt.get_fignums())
    try:
        if not redo.wait(timeout=5.0) or not trad.is_alive():
            fel = "Mottagaren kunde inte startas. " + " ".join(mottagarfel)
        else:
            time.sleep(0.2)
            send_symbols(ram, step_time=bittid, levels=NIVAER)
    except Exception as exc:
        fel = f"Sändningen misslyckades: {exc}"
    finally:
        trad.join(timeout=timeout_s + 3)
        for nummer in set(plt.get_fignums()) - tidigare_figurer:
            plt.close(nummer)
    if trad.is_alive():
        raise RuntimeError("Mottagaren avslutades inte. Avbryter serietestet.")
    tid = time.perf_counter() - start
    korrekt = False
    if not fel:
        try:
            if mottagarfel:
                raise RuntimeError(" ".join(mottagarfel))
            if len(mottagna) != len(nyttobitar):
                raise ValueError("Ingen fullständig ram eller fel antal nyttobitar.")
            bitar = decode(mottagna, H, padding) if hamming else mottagna
            korrekt = huffman_decode(bitar, tree) == text
            if not korrekt:
                fel = "Mottagen text skiljer sig från originalet."
        except Exception as exc:
            fel = str(exc)
    return korrekt, tid, len(nyttobitar), len(mottagna), fel


def serietest():
    from pathlib import Path
    from datetime import datetime

    text = input("Text att upprepa vid varje inställning: ").lower()
    if not text:
        print("Texten får inte vara tom.")
        return
    huffman_encode(text, codes)
    antal = las_positivt_tal("Antal meddelanden per inställning: ", heltal=True)
    if CLI_BITTID is not None:
        bittider = [CLI_BITTID]
    else:
        while True:
            try:
                bittider = [float(v.replace(",", ".")) / 1000
                            for v in input("Symboltider i ms, separerade med mellanslag (t.ex. 4 3 2,5): ").split()]
                if bittider and all(np.isfinite(t) and t >= 0.00025 for t in bittider):
                    break
            except ValueError:
                pass
            print("Ange minst en symboltid, minst 0,25 ms.")
    while True:
        val = input("Hamming: 1 = utan, 2 = med, 3 = båda: ").strip()
        if val in ("1", "2", "3"):
            break
        print("Välj 1, 2 eller 3.")
    lagen = {"1": [False], "2": [True], "3": [False, True]}[val]
    filnamn = Path("felsok_serie_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".csv")
    print(f"Tröskel: {TROSKEL} V. Resultat sparas i {filnamn}")
    print("Tiden inkluderar uppstart, sändarens diagramvisning och eventuell timeout.")
    print("Ingen automatisk omsändning. Samma text skickas i varje försök.")
    with filnamn.open("x", newline="", encoding="utf-8") as fil:
        writer = csv.writer(fil)
        writer.writerow(["symboltid_ms", "hamming", "nivaer", "trosklar_V", "forsok", "korrekt",
                         "tid_s", "sanda_nyttobitar", "mottagna_nyttobitar", "korrekta_tecken", "fel"])
        for bittid in bittider:
            for hamming in lagen:
                lyckade = 0
                totaltid = 0.0
                print(f"\n--- {bittid * 1000:g} ms, Hamming {'på' if hamming else 'av'} ---")
                for forsok in range(1, antal + 1):
                    korrekt, tid, sanda, mottagna, fel = kor_serietest(text, bittid, hamming)
                    lyckade += int(korrekt)
                    totaltid += tid
                    writer.writerow([bittid * 1000, int(hamming), NIVAER, TROSKEL, forsok,
                                     int(korrekt), tid, sanda, mottagna,
                                     len(text) if korrekt else 0, fel])
                    fil.flush()
                    print(f"{forsok}/{antal}: {'OK' if korrekt else fel} ({tid:.2f} s)")
                print(f"Resultat: {lyckade}/{antal} korrekta ({100 * lyckade / antal:.1f} %), "
                      f"{lyckade * len(text) / totaltid:.2f} korrekta tecken/s.")
    print(f"\nSerietest klart. Resultat: {filnamn}")


def main(argv=None):
    global BITTID, TROSKEL, NIVAER, AI_FREKVENS, CLI_BITTID
    args = communication_args(argv, "Felsök optisk kommunikation", default_threshold=3.0)
    BITTID, TROSKEL, NIVAER = args.step_time, args.threshold, args.levels
    CLI_BITTID = BITTID if args.step_time_explicit else None
    AI_FREKVENS = max(25, round(5000 * BITTID)) / BITTID
    print(f"{NIVAER} nivåer, {BITTID:g} s/symbol, trösklar {TROSKEL} V")
    print("1. Befintlig felsökning: ett meddelande, analoga sampel och diagram")
    print("2. Serietest: flera meddelanden per inställning")
    while True:
        val = input("Välj 1 eller 2 [1]: ").strip() or "1"
        if val == "1":
            enstaka_felsokning()
            return
        if val == "2":
            serietest()
            return
        print("Välj 1 eller 2.")


if __name__ == "__main__":
    main()
