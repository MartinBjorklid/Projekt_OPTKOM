"""Kör med: python spanningssvep.py. Kräver nidaqmx, numpy och matplotlib."""

import csv
import time
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import nidaqmx
import numpy as np
from nidaqmx.constants import AcquisitionType, TerminalConfiguration

AO = "Dev1/ao0"
AI = "Dev1/ai0"  # Fotodiodens signal efter förstärkaren (TIA).
STEG = 0.25       # V mellan mätpunkter; ska dela 5 jämnt.
VANTETID = 0.2   # Sekunder för signalen att stabiliseras efter varje steg.
SAMPLING = 2000  # Sampel/s.
ANTAL = 200      # 0,1 sekunders mätning per steg.


def main():
    n = round(5 / STEG)
    if STEG <= 0 or not np.isclose(n * STEG, 5):
        raise ValueError("STEG måste vara positivt och dela 5 jämnt.")
    delar = [np.linspace(0, 5, n + 1),
             np.linspace(5, 0, n + 1)[1:],
             np.linspace(0, -5, n + 1)[1:]]
    etiketter = ["0 → +5 V", "+5 → 0 V", "0 → −5 V"]
    fil = Path(f"spanningssvep_{datetime.now():%Y%m%d_%H%M%S_%f}.csv")
    resultat = []
    with fil.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["del", "ut_V", "medel_V", "std_V", "min_V", "max_V"])
        with nidaqmx.Task() as ao, nidaqmx.Task() as ai:
            ao.ao_channels.add_ao_voltage_chan(AO, min_val=-5, max_val=5)
            try:
                ao.write(0.0)
                ai.ai_channels.add_ai_voltage_chan(
                    AI, terminal_config=TerminalConfiguration.RSE,
                    min_val=-10, max_val=10)
                ai.timing.cfg_samp_clk_timing(
                    SAMPLING, sample_mode=AcquisitionType.FINITE,
                    samps_per_chan=ANTAL)
                for delnummer, spanningar in enumerate(delar):
                    for ut in spanningar:
                        ao.write(float(ut))
                        time.sleep(VANTETID)
                        # Ny mätning efter väntetiden: inga gamla buffrade sampel.
                        ai.start()
                        v = np.asarray(ai.read(ANTAL, timeout=5))
                        ai.stop()
                        rad = [delnummer, ut, v.mean(), v.std(ddof=1), v.min(), v.max()]
                        resultat.append(rad)
                        writer.writerow(rad)
                        f.flush()
                        print(f"Ut: {ut:+5.2f} V | Fotodiod: {rad[2]:.4f} V | Brus (std): {rad[3]:.4f} V")
            finally:
                ao.write(0.0)  # Återställ även vid fel eller Ctrl+C.

    data = np.asarray(resultat)
    print(f"Uppmätt medelnivå: {data[:, 2].min():.4f}–{data[:, 2].max():.4f} V")
    print(f"Mätdata sparade i {fil.resolve()}")
    for i, etikett in enumerate(etiketter):
        d = data[data[:, 0] == i]
        plt.errorbar(d[:, 1], d[:, 2], yerr=3 * d[:, 3],
                     fmt=".-", capsize=3, label=etikett)
    plt.xlabel("Styrspänning AO [V]")
    plt.ylabel("Fotodiodens förstärkta signal AI [V]")
    plt.title("Fotodiodens nivåer — felstaplar visar ±3 standardavvikelser")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
