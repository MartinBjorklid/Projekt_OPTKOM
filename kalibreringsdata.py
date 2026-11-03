"""Kalibrering från gränssnittet.

Kör samma svep och långa test som kalibrering.py (main) och sparar resultatet
i parametrar.py med kalibrering.spara. Läser också parametrar.py, så att
gränssnittet kan använda de senast kalibrerade värdena vid uppstart.
"""
import re
import runpy
import tempfile
from pathlib import Path
from unittest import mock

import numpy as np

import kalibrering
from skicka import bygg_ram
from taemot import samplingsparametrar


def las_parametrar():
    """
    Läser parametrar.py på nytt från disk (den kan ha skrivits om av en kalibrering).
    Returnerar step_time, threshold, kalibreringstid och kommentarraderna i filen.
    """
    varden = runpy.run_path(str(kalibrering.PARAMETERFIL))
    text = kalibrering.PARAMETERFIL.read_text(encoding="utf-8")
    kommentarer = [rad.lstrip("# ").rstrip() for rad in text.splitlines() if rad.startswith("#")]
    tid = re.search(r"Kalibrerad (\d{4}-\d{2}-\d{2} \d{2}:\d{2})", text)
    return {
        "step_time": float(varden["step_time"]),
        "threshold": float(varden["threshold"]),
        "tid": tid.group(1) if tid else None,
        "kommentarer": kommentarer,
        "fil": str(kalibrering.PARAMETERFIL),
    }


def _simulerad_matning(step_time, nyttobitar):
    """
    Ersätter kalibrering.mat i simuleringsläget: samma ram och vilotider,
    men nivåerna och bruset från gränssnittets simulering (0,2/4,8 V, 0,1 V brus).
    """
    _, ai_rate = samplingsparametrar(step_time)
    n = ai_rate * step_time
    vila = int(np.ceil(kalibrering.VILOTID * ai_rate))
    ram = np.asarray(bygg_ram(nyttobitar))
    nivaer = np.where(np.repeat(ram, int(round(n))) == 1, 4.8, 0.2)
    signal = np.concatenate([np.full(vila, 0.2), nivaer, np.full(2 * vila, 0.2)])
    signal = signal + np.random.default_rng().normal(0, 0.1, len(signal))
    return signal, n, ai_rate


def kor_kalibrering(simulera, framsteg=None):
    """
    Kör kalibreringen som kalibrering.main gör. framsteg(text, andel) anropas under tiden.
    En riktig kalibrering sparas i parametrar.py; en simulerad sparas inte.
    Returnerar {"vald": resultat eller None, "svep": [resultat per frekvens], "meddelande": text}.
    """
    def visa(text, andel):
        if framsteg is not None:
            framsteg(text, min(1.0, andel))

    patchar = []
    if simulera:
        # Simulerade mätningar, och felsökningsfilen skrivs inte över med testdata
        tempfil = Path(tempfile.mkdtemp()) / "kalibrering_simulerad.npz"
        patchar = [mock.patch.object(kalibrering, "mat", _simulerad_matning),
                   mock.patch.object(kalibrering, "TESTFIL", tempfil)]
    for p in patchar:
        p.start()
    try:
        godkanda, svep = [], []
        for i, frekvens in enumerate(kalibrering.FREKVENSER):
            visa(f"Svep: {frekvens} bit/s", 0.8 * i / len(kalibrering.FREKVENSER))
            r = kalibrering.svep(frekvens)
            svep.append(r)
            if r["giltiga"]:
                godkanda.append(r)
            elif godkanda:
                # Sluta vid första misslyckandet efter godkända frekvenser
                break

        if not godkanda:
            return {"vald": None, "svep": svep,
                    "meddelande": "Ingen frekvens fungerade. Kontrollera kopplingen, ljuskällan och att en "
                                  "etta ger HÖGRE spänning på ai0 än vila."}

        tak = (1 - kalibrering.SAKERHETSMARGINAL) * godkanda[-1]["frekvens"]
        kandidater = sorted((r for r in godkanda if r["frekvens"] <= tak),
                            key=lambda r: -r["frekvens"]) or [godkanda[0]]
        vald = None
        for r in kandidater:
            visa(f"Långt test vid {r['frekvens']} bit/s ({kalibrering.LANGT_TEST_BITAR} bitar)", 0.85)
            if kalibrering.langt_test(r):
                vald = r
                break
        if vald is None:
            return {"vald": None, "svep": svep,
                    "meddelande": "Inget långt test klarades. Granska kalibrering.npz med felsok.py."}

        if not simulera:
            kalibrering.spara(vald)
        visa("Klar", 1.0)
        return {"vald": vald, "svep": svep, "meddelande": None}
    finally:
        for p in patchar:
            p.stop()
