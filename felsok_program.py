"""Felsök samma överföring som program.py och spara mätdata.

Lägg filen bredvid program.py, skicka.py, taemot.py och Signalbehandling.py.
Kör: python felsok_program.py

Samma Huffman + Hamming (7,4), ram, send_binary_list(), bittid, AI0,
2,3 V-tröskel, DAQ-inställningar och SynkadBitavkodare som program.py.
Mottagarloopen nedan motsvarar receive_continuous() i taemot.py på main
vid tillägget av denna fil; den sparar även avkodarens analoga sampel.
Facit används ENDAST för jämförelsen efter mottagningen, aldrig för synk.

Varje körning sparar ai0.csv, bitar.csv och sammanfattning.json under
felsok_program_resultat/. Om sampel finns sparas också ett diagram.png.
Inspelningen slutar vid fullständig ram eller timeout, som i program.py;
den omfattar inte nödvändigtvis sändarens efterföljande nollor.
Kör inte andra program som använder DAQ-kanalerna samtidigt.
"""

import csv
import json
import threading
import time
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import nidaqmx
import numpy as np
from nidaqmx.constants import AcquisitionType, TerminalConfiguration

from skicka import Start_seq, Slut_seq, send_binary_list, step_time
from taemot import SynkadBitavkodare
from Signalbehandling import codes, tree, huffman_encode, huffman_decode, encode, decode


AI_KANAL = "Dev1/ai0"
TROSKEL = 2.3


class Matning:
    def __init__(self):
        self.avkodare = None
        self.sample_rate = None
        self.sampel_per_bit = None
        self.fel = ""


def ta_emot_med_matning(matning, step_time=step_time, channel=AI_KANAL,
                       threshold=TROSKEL, timeout_s=15.0, ready_event=None):
    """Samma mottagarflöde som taemot.receive_continuous(), med sparad diagnostik."""
    try:
        if step_time <= 0:
            raise ValueError("step_time måste vara positiv.")
        sampel_per_bit = max(25, int(round(5000 * step_time)))
        sample_rate = sampel_per_bit / step_time
        if sample_rate > 100_000:
            raise ValueError("För kort bittid för denna DAQ och vald översampling.")

        avkodare = SynkadBitavkodare(sampel_per_bit, threshold)
        matning.avkodare = avkodare
        matning.sample_rate = sample_rate
        matning.sampel_per_bit = sampel_per_bit
        deadline = time.monotonic() + timeout_s
        print(f"Lyssnar på {channel}: {sample_rate:.0f} sampel/s, "
              f"{sampel_per_bit} sampel/bit, tröskel {threshold:g} V.")

        with nidaqmx.Task() as task:
            task.ai_channels.add_ai_voltage_chan(
                channel, terminal_config=TerminalConfiguration.RSE,
                min_val=-10.0, max_val=10.0,
            )
            task.timing.cfg_samp_clk_timing(
                rate=sample_rate,
                sample_mode=AcquisitionType.CONTINUOUS,
                samps_per_chan=int(sample_rate * 2),
            )
            task.in_stream.input_buf_size = int(sample_rate * 10)
            task.start()
            if ready_event is not None:
                ready_event.set()

            while time.monotonic() < deadline:
                block = task.read(
                    number_of_samples_per_channel=max(sampel_per_bit, int(sample_rate * 0.02)),
                    timeout=2.0,
                )
                avkodare.mata_in(block)
                if avkodare.klar:
                    print(f"Slutsekvens mottagen. {len(avkodare.payload)} nyttobitar.")
                    return avkodare.payload

        matning.fel = "Timeout: ingen fullständig ram mottagen."
    except Exception as exc:
        matning.fel = f"Mottagarfel: {exc}"
    finally:
        if ready_event is not None:
            ready_event.set()
    print(matning.fel)
    return []


def spara_diagnostik(matning, ram, nyttobitar, mottagna, sammanfattning):
    """Spara även misslyckade försök. Rita först när DAQ-mottagningen avslutats."""
    rot = Path("felsok_program_resultat")
    rot.mkdir(exist_ok=True)
    mapp = rot / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    mapp.mkdir()
    avkodare = matning.avkodare
    signal = np.asarray(avkodare.data if avkodare is not None else [], dtype=float)
    start_index = avkodare.start_index if avkodare is not None else None
    klar = bool(avkodare is not None and avkodare.klar)
    n = matning.sampel_per_bit
    rate = matning.sample_rate
    lika_langd = len(mottagna) == len(nyttobitar)
    # BER kräver en fullständig ram med jämförbar nyttolängd.
    bitfel = sum(a != b for a, b in zip(nyttobitar, mottagna)) if klar and lika_langd else None
    sammanfattning.update({
        "bittid_s": step_time, "sandningsfrekvens_Hz": 1.0 / step_time,
        "ai_kanal": AI_KANAL, "terminalkonfiguration": "RSE", "troskel_V": TROSKEL,
        "ai_frekvens_Hz": rate, "sampel_per_bit": n, "hamming": True,
        "sanda_nyttobitar": len(nyttobitar), "mottagna_nyttobitar": len(mottagna),
        "fullstandig_ram": klar, "ratt_nyttolangd": klar and lika_langd,
        "bitfel_fore_hamming": bitfel,
        "BER_fore_hamming": bitfel / len(nyttobitar) if bitfel is not None and nyttobitar else None,
        "start_sampel": start_index,
        "start_tid_s": start_index / rate if start_index is not None else None,
        "antal_ai_sampel": len(signal), "mottagarfel": matning.fel,
    })
    with (mapp / "sammanfattning.json").open("w", encoding="utf-8") as fil:
        json.dump(sammanfattning, fil, ensure_ascii=False, indent=2)
    with (mapp / "ai0.csv").open("w", newline="", encoding="utf-8") as fil:
        writer = csv.writer(fil)
        writer.writerow(["sampel", "tid_s", "ai0_V"])
        for i, v in enumerate(signal):
            writer.writerow([i, i / rate, float(v)])

    # Avlästa mittvärden från just den start som den verkliga avkodaren hittade.
    # Rader efter en förtida slutsekvens är bara efteranalys, inte mottagna nyttobitar.
    tidpunkter, volt, avlasta = [], [], []
    with (mapp / "bitar.csv").open("w", newline="", encoding="utf-8") as fil:
        writer = csv.writer(fil)
        writer.writerow(["rambit", "del", "sand_bit", "tid_s", "ai0_median_V",
                         "avlast_bit", "fel", "behandlad_av_mottagaren"])
        for j, sand in enumerate(ram):
            delnamn = ("start" if j < len(Start_seq) else
                       "nyttodata" if j < len(Start_seq) + len(nyttobitar) else "slut")
            if start_index is None or start_index + (j + 1) * n > len(signal):
                writer.writerow([j, delnamn, sand, "", "", "", "", 0])
                continue
            a = start_index + j * n + n // 3
            b = start_index + j * n + 2 * n // 3
            v = float(np.median(signal[a:b]))
            mott = int(v >= TROSKEL)
            t = (a + b - 1) / (2 * rate)
            behandlad = j < avkodare.nasta_bit
            writer.writerow([j, delnamn, sand, t, v, mott, int(mott != sand), int(behandlad)])
            tidpunkter.append(t)
            volt.append(v)
            avlasta.append(mott)

    print(f"\nDiagnostik sparad i: {mapp}")
    if bitfel is not None:
        print(f"Bitfel före Hamming: {bitfel} av {len(nyttobitar)} nyttobitar")
    else:
        print("Bitfel/BER före Hamming kan inte beräknas: ingen fullständig ram med rätt längd.")
    if not len(signal):
        return mapp

    tid = np.arange(len(signal)) / rate
    fig, (ax0, ax1) = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    ax0.plot(tid, signal, linewidth=0.8, label="Uppmätt AI0")
    ax0.axhline(TROSKEL, color="tab:red", linestyle="--", label=f"Tröskel {TROSKEL:g} V")
    if tidpunkter:
        ax0.scatter(tidpunkter, volt, s=12, color="black", label="Median i bitens mitt")
        fel = np.asarray(avlasta) != np.asarray(ram[:len(avlasta)])
        if np.any(fel):
            ax0.scatter(np.asarray(tidpunkter)[fel], np.asarray(volt)[fel],
                        color="tab:red", marker="x", label="Bitfel före Hamming")
        bit_tid = (start_index + np.arange(len(ram)) * n) / rate
        # Sista punkten ger även sista biten sin fulla bredd.
        kanter = np.append(bit_tid, bit_tid[-1] + step_time)
        ax1.step(kanter, ram + [ram[-1]], where="post", label="Sänd ram (facit)")
        last_tid = (start_index + np.arange(len(avlasta) + 1) * n) / rate
        ax1.step(last_tid, np.asarray(avlasta + [avlasta[-1]]) + 0.05,
                 where="post", label="Avläst från AI0 (+0,05)", alpha=0.8)
        ax0.axvline(start_index / rate, color="tab:green", linestyle=":", label="Upptäckt start")
        ax1.legend(loc="upper right")
    else:
        ax1.text(0.5, 0.5, "Ingen startsekvens hittad", ha="center", transform=ax1.transAxes)
    ax0.set_ylabel("AI0 [V]")
    ax0.legend(loc="upper right")
    ax0.grid(True)
    ax1.set_ylabel("Bit")
    ax1.set_xlabel("Tid från inspelningsstart [s]")
    ax1.set_ylim(-0.2, 1.35)
    ax1.set_xlim(0, max(step_time, float(tid[-1])))
    ax1.grid(True)
    fig.tight_layout()
    fig.savefig(mapp / "diagram.png", dpi=150)
    plt.show()
    plt.close(fig)
    return mapp


def main():
    text = input("Skriv ett meddelande: ").lower()
    komprimerade_bitar = huffman_encode(text, codes)
    kodade_bitar, H, padding = encode(komprimerade_bitar)
    nyttobitar = kodade_bitar.tolist()
    # Samma kontroll som program.py; protokollet ändras inte av felsökningen.
    for i in range(len(nyttobitar) - len(Slut_seq) + 1):
        if nyttobitar[i:i + len(Slut_seq)] == Slut_seq:
            print("Slutsekvensen förekommer i nyttodatan. Välj annat meddelande "
                  "för detta test, eller byt till ett protokoll med längdfält.")
            return

    ram = Start_seq + nyttobitar + Slut_seq
    timeout_s = max(15.0, len(ram) * step_time + 6)
    print(f"Bittid: {step_time * 1000:g} ms ({1 / step_time:g} bit/s). "
          f"Huffman + Hamming: {len(nyttobitar)} nyttobitar, {len(ram)} rambitar.")
    matning = Matning()
    redo = threading.Event()
    mottagna_bitar = []
    sammanfattning = {"originaltext": text, "avkodad_text": None,
                     "stammer_med_original": False, "padding": padding,
                     "timeout_s": timeout_s, "sandarfel": "", "avkodningsfel": ""}

    def lyssna():
        nonlocal mottagna_bitar
        mottagna_bitar = ta_emot_med_matning(
            matning, step_time=step_time, channel=AI_KANAL, threshold=TROSKEL,
            timeout_s=timeout_s, ready_event=redo,
        )

    trad = threading.Thread(target=lyssna)
    trad.start()
    try:
        if not redo.wait(timeout=5.0):
            sammanfattning["sandarfel"] = "Mottagaren kunde inte startas inom fem sekunder."
            print("Mottagaren kunde inte startas. Avbryter sändningen.")
        elif not trad.is_alive():
            sammanfattning["sandarfel"] = "Mottagaren kunde inte startas."
            print("Mottagaren kunde inte startas. Kontrollera DAQ och felutskriften.")
        else:
            time.sleep(0.2)
            print("\n[MAIN] Startar sändningen...")
            send_binary_list(ram)
    except Exception as exc:
        sammanfattning["sandarfel"] = str(exc)
        print(f"Sändningen misslyckades: {exc}")
    finally:
        trad.join(timeout=timeout_s + 3)
    if trad.is_alive():
        print("Mottagaren avslutades inte inom tidsgränsen. Vänta tills den avslutats före nästa körning.")
        return

    print(f"\nMottagna nyttobitar: {len(mottagna_bitar)} / {len(nyttobitar)}")
    if not mottagna_bitar:
        print("Ingen fullständig ram mottagen.")
    else:
        if len(mottagna_bitar) != len(nyttobitar):
            print("VARNING: Fel antal nyttobitar. Kontrollera synkronisering eller falsk slutsekvens.")
        try:
            avkodade_bitar = decode(mottagna_bitar, H, padding)
            avkodad = huffman_decode(avkodade_bitar, tree)
            sammanfattning["avkodad_text"] = avkodad
            sammanfattning["stammer_med_original"] = avkodad == text
            print("Avkodad text:", avkodad)
            print("Stämmer med original:", avkodad == text)
        except Exception as exc:
            sammanfattning["avkodningsfel"] = str(exc)
            print(f"Avkodningen misslyckades: {exc}")
    spara_diagnostik(matning, ram, nyttobitar, mottagna_bitar, sammanfattning)


if __name__ == "__main__":
    main()
