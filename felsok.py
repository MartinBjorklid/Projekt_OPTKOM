"""Felsökning av mottagningen.

taemot.py sparar varje mottagning i senaste_mottagning.npz (rådata från ai0,
var varje bit lästes, bitmedianer och klockjusteringar). Kör

    python felsok.py                     # senaste mottagningen
    python felsok.py kalibrering.npz     # valfri sparad fil

för en sammanfattning och tre grafer:
  1. ai0-signalen med varje bits avläsningspunkt (röd = fel bit),
  2. bitmedianer mot bitnummer – visar om nivåerna sjunker under sändningen,
  3. klockföljningens justeringar – visar om samplingen hade glidit utan den.
"""

import sys
from pathlib import Path

import numpy as np

FELSOKFIL = Path(__file__).with_name("senaste_mottagning.npz")


def spara(fil, avkodare, ai_rate, skickade_bitar=None):
    """Spara avkodarens tillstånd för senare analys."""
    np.savez_compressed(
        fil,
        signal=np.asarray(avkodare.data, dtype=float),
        bitpositioner=np.asarray(avkodare.bitpositioner, dtype=float),
        medianer=np.asarray(avkodare.medianer, dtype=float),
        fasfel=np.asarray(avkodare.fasfel, dtype=float).reshape(-1, 2),
        threshold=avkodare.threshold,
        n=avkodare.n,
        ai_rate=ai_rate,
        skickat=np.asarray(skickade_bitar if skickade_bitar is not None else [], dtype=int),
    )


def sammanfatta(fil=FELSOKFIL):
    """Skriv ut bitfel och klockavvikelse. Returnerar den laddade datan."""
    d = dict(np.load(fil))
    bitar = (d["medianer"] >= d["threshold"]).astype(int)
    print(f"{fil}: {len(bitar)} bitar avlästa, tröskel {float(d['threshold']):.3f} V, "
          f"{float(d['n']):.3f} AI-sampel/bit")
    if len(bitar) == 0:
        print("Ingen startsekvens hittades.")
        return d

    skickat = d["skickat"]
    if len(skickat):
        k = min(len(bitar), len(skickat))
        fel = np.flatnonzero(bitar[:k] != skickat[:k])
        print(f"Bitfel: {len(fel)} av {k}"
              + (f", första vid bit {fel[0]}" if len(fel) else "")
              + (f" (mottog {len(bitar)} bitar, skickade {len(skickat)})"
                 if len(bitar) != len(skickat) else ""))

    ettor, nollor = d["medianer"][bitar == 1], d["medianer"][bitar == 0]
    if len(ettor) and len(nollor):
        print(f"Marginal till tröskeln: svagaste etta {ettor.min() - d['threshold']:+.3f} V, "
              f"starkaste nolla {nollor.max() - d['threshold']:+.3f} V")

    korrigering = d["bitpositioner"] - (d["bitpositioner"][0] + np.arange(len(bitar)) * d["n"])
    if len(bitar) > 100:
        lutning = np.polyfit(np.arange(len(bitar)), korrigering, 1)[0]
        print(f"Klockföljningen flyttade samplingen totalt {korrigering[-1]:+.1f} sampel "
              f"(≈ {lutning / d['n'] * 1e6:+.0f} ppm klockavvikelse).")
    return d


def plotta(fil=FELSOKFIL):
    import matplotlib.pyplot as plt

    d = sammanfatta(fil)
    if len(d["medianer"]) == 0:
        return
    n, troskel, ai_rate = float(d["n"]), float(d["threshold"]), float(d["ai_rate"])
    bitar = (d["medianer"] >= troskel).astype(int)
    index = np.arange(len(bitar))
    skickat = d["skickat"]
    fel = np.zeros(len(bitar), dtype=bool)
    if len(skickat):
        k = min(len(bitar), len(skickat))
        fel[:k] = bitar[:k] != skickat[:k]

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(13, 9))

    t = np.arange(len(d["signal"])) / ai_rate
    mitt = (d["bitpositioner"] + n / 2) / ai_rate
    ax1.plot(t, d["signal"], linewidth=0.8, label="ai0")
    ax1.axhline(troskel, color="red", linestyle="--", label="threshold")
    ax1.scatter(mitt[~fel], d["medianer"][~fel], s=8, color="green", label="avläst bit")
    if fel.any():
        ax1.scatter(mitt[fel], d["medianer"][fel], s=20, color="red", label="fel bit")
    ax1.set_xlabel("Tid [s]")
    ax1.set_ylabel("Spänning [V]")
    ax1.set_title("Signal och avläsningspunkter (zooma in för att se enskilda bitar)")
    ax1.legend(loc="upper right")

    ax2.scatter(index[bitar == 1], d["medianer"][bitar == 1], s=4, label="ettor")
    ax2.scatter(index[bitar == 0], d["medianer"][bitar == 0], s=4, label="nollor")
    ax2.axhline(troskel, color="red", linestyle="--", label="threshold")
    if fel.any():
        ax2.scatter(index[fel], d["medianer"][fel], s=25, facecolors="none",
                    edgecolors="red", label="fel bit")
    ax2.set_xlabel("Bitnummer")
    ax2.set_ylabel("Bitmedian [V]")
    ax2.set_title("Nivåer under sändningen (sjunkande ettor = drift i ljus/LC)")
    ax2.legend(loc="upper right")

    korrigering = d["bitpositioner"] - (d["bitpositioner"][0] + index * n)
    ax3.plot(index, korrigering, label="total klockjustering")
    if len(d["fasfel"]):
        ax3.scatter(d["fasfel"][:, 0], d["fasfel"][:, 1], s=4, color="gray",
                    label="flankfel vid stigande flank")
    for grans in (n / 3, -n / 3):
        ax3.axhline(grans, color="red", linestyle=":", linewidth=0.8)
    ax3.set_xlabel("Bitnummer")
    ax3.set_ylabel("Sampel")
    ax3.set_title("Klockföljning (röda linjer = så långt samplingen tål att glida utan den)")
    ax3.legend(loc="upper left")

    for ax in (ax1, ax2, ax3):
        ax.grid(True)
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    plotta(sys.argv[1] if len(sys.argv) > 1 else FELSOKFIL)
