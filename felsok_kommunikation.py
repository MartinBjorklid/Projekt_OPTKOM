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
import nidaqmx
import numpy as np
from nidaqmx.constants import AcquisitionType, TerminalConfiguration

from skicka import Start_seq, Slut_seq, send, step_time
from Signalbehandling import codes, huffman_encode


# Samma bittid och tröskel som er nuvarande program.py / taemot.py.
BITTID = step_time              # s per bit
AI_FREKVENS = 1000        # Hz; fler sampel än i ordinarie mottagare
TROSKEL = 3.0             # V: 1 om AI0 >= TROSKEL
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
                    samps_per_chan=AI_FREKVENS * 2,
                )
                task.in_stream.input_buf_size = AI_FREKVENS * 10
                task.start()
                self.klar_att_mata.set()

                # Läs regelbundet: vi ska inte hoppa över övergångar.
                while not self.stoppa.is_set():
                    data = task.read(
                        number_of_samples_per_channel=max(1, AI_FREKVENS // 10),
                        timeout=2.0,
                    )
                    self.block.append(np.asarray(data, dtype=float))

        except Exception as exc:
            self.fel = exc
            self.klar_att_mata.set()


def fast_blockavkodning(signal, sampel_per_bit):
    """Ungefär samma 0,1-sekundersblock som taemot.py, utan faslåsning.

    Här startar blocken vid felsökningsinspelningens början. Detta är inte en
    exakt återspelning av en separat körning av taemot.py, som startar sin egen
    DAQ-klocka och därmed kan få *annan* blockfas.
    """
    antal = len(signal) // sampel_per_bit
    medel = signal[:antal * sampel_per_bit].reshape(
        antal, sampel_per_bit
    ).mean(axis=1)
    bitar = (medel >= TROSKEL).astype(int)

    start = np.asarray(Start_seq, dtype=int)
    slut = np.asarray(Slut_seq, dtype=int)
    for i in range(len(bitar) - len(start) + 1):
        if np.array_equal(bitar[i:i + len(start)], start):
            for j in range(i + len(start), len(bitar) - len(slut) + 1):
                if np.array_equal(bitar[j:j + len(slut)], slut):
                    return i, j, bitar[i + len(start):j], medel
            return i, None, None, medel
    return None, None, None, medel


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
        bitar = (v >= TROSKEL).astype(int)
        antal_ratt = (bitar == facit[None, :]).sum(axis=1)
        pos = int(np.argmax(antal_ratt))
        resultat = (int(antal_ratt[pos]), int(starter[pos]))
        if bast is None or resultat[0] > bast[0]:
            bast = resultat

    _, start_sample = bast
    centrum_index = start_sample + mittpunkter
    volt = filtrerat[centrum_index]
    bitar = (volt >= TROSKEL).astype(int)
    return start_sample, centrum_index, volt, bitar


def spara_och_rita(signal, facit):
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
    print("\n--- Fasta 0,1 s-block (liknar nuvarande taemot.py) ---")
    if block_start is None:
        print("Hittade INTE startsekvensen med dessa blockgränser.")
    elif block_slut is None:
        print(f"Start hittad vid block {block_start}, men INGEN slutsekvens.")
    else:
        facit_payload = facit[len(Start_seq):-len(Slut_seq)]
        print(f"Start i block {block_start}, slut i block {block_slut}.")
        print(f"Mottaget antal nyttobitar: {len(block_payload)}; förväntat: {len(facit_payload)}")
        print(f"Nyttobitarna överensstämmer: {np.array_equal(block_payload, facit_payload)}")

    start_sample, centrum_index, volt, bitar = hitta_basta_bitfas(
        signal, facit, sampel_per_bit
    )
    fel_index = np.flatnonzero(bitar != facit)
    v0 = volt[facit == 0]
    v1 = volt[facit == 1]

    print("\n--- Bitfas optimerad MOT det kända sända meddelandet ---")
    print(f"Uppskattad start: {start_sample / AI_FREKVENS:.3f} s efter inspelningsstart")
    print(f"Fel vid tröskel {TROSKEL:g} V: {len(fel_index)} av {len(facit)} bitar")
    if len(v0) and len(v1):
        print(f"Median AI0 för sända 0:or = {np.median(v0):.3f} V")
        print(f"Median AI0 för sända 1:or = {np.median(v1):.3f} V")
        print(f"Spänningsintervall 0: {v0.min():.3f} ... {v0.max():.3f} V")
        print(f"Spänningsintervall 1: {v1.min():.3f} ... {v1.max():.3f} V")
    if len(fel_index):
        print("Första felaktiga bitpositionerna (0-baserade):", fel_index[:25].tolist())
        print("Facit på dessa positioner:", facit[fel_index[:25]].tolist())
        print("Avläst på dessa positioner:", bitar[fel_index[:25]].tolist())

    with open("felsok_bitar.csv", "w", newline="", encoding="utf-8") as fil:
        writer = csv.writer(fil)
        writer.writerow(["tid_s", "sant_bitvarde", "avlast_bitvarde", "ai0_mitt_V", "fel"])
        for index, v, s, mott in zip(centrum_index, volt, facit, bitar):
            writer.writerow([index / AI_FREKVENS, int(s), int(mott), float(v), int(s != mott)])
    print("Sparade bitjämförelsen i felsok_bitar.csv")

    # Två diagram med samma tidsaxel: analog signal och digital tolkning.
    fig, (ax0, ax1) = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    ax0.plot(t, signal, linewidth=0.8, label="Uppmätt AI0")
    ax0.axhline(TROSKEL, color="tab:red", linestyle="--", label=f"Tröskel {TROSKEL:g} V")
    ax0.scatter(centrum_index / AI_FREKVENS, volt, s=12, color="black", label="Bitars mittpunkter")
    if len(fel_index):
        ax0.scatter(centrum_index[fel_index] / AI_FREKVENS, volt[fel_index],
                    s=35, color="tab:red", marker="x", label="Feltolkade bitar")
    ax0.set_ylabel("TIA-utgång AI0 [V]")
    ax0.legend(loc="upper right")
    ax0.grid(True)

    bit_tid = (start_sample + np.arange(len(facit)) * sampel_per_bit) / AI_FREKVENS
    ax1.step(bit_tid, facit, where="post", label="Sänd bit (facit)", linewidth=1.8)
    ax1.step(bit_tid, bitar + 0.05, where="post", label="AI0 tolkad bit (+0,05 för synlighet)", alpha=0.75)
    ax1.set_ylim(-0.2, 1.35)
    ax1.set_xlabel("Tid från inspelningsstart [s]")
    ax1.set_ylabel("Bit")
    ax1.legend(loc="upper right")
    ax1.grid(True)
    ax1.set_xlim(max(0, bit_tid[0] - 0.2), min(t[-1], bit_tid[-1] + BITTID + 0.2))
    fig.tight_layout()
    plt.show()


def main():
    text = input("Text att skicka och felsöka: ").lower()
    # Använd exakt samma kodtabell och ram som er befintliga sändare.
    facit = np.asarray(Start_seq + huffman_encode(text, codes) + Slut_seq, dtype=int)
    if len(facit) * BITTID > 180:
        raise ValueError("Välj ett kortare meddelande till första felsökningstestet.")
    print(f"Sänder {len(facit)} bitar ({len(facit) * BITTID:.1f} s), AI0 {AI_FREKVENS} sampel/s")
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
        send(text)  # Samma skicka.py som används av ert program.py
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
    spara_och_rita(signal, facit)


if __name__ == "__main__":
    main()
