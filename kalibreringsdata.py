"""Kalibrering från gränssnittet.

Använder mätningen och analysen i kalibrering/kalibrering.py och sparar resultatet
i samma format (rapport.json + matdata.npz, en ny mapp per körning). Kalibreringar
som körs från kommandoraden i mappen kalibrering/ hittas därför också.
"""
import json
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np

from kalibrering.kalibrering import training_pattern, capture, analyse
from taemot import berakna_sampel_per_bit

RESULTATMAPP = Path(__file__).resolve().parent / "kalibrering" / "kalibreringsresultat"

# Samma standardvärden som kalibrering.py
VILOTID = 0.2           # s inspelning före testmönstret
MAX_FORDROJNING = 0.5   # s sökfönster för program- och optisk fördröjning
MIN_MARGINAL = 0.1      # V minsta avstånd mellan nollornas och ettornas nivåer


def _simulera_matning(bitar, step_time, sample_rate):
    """Testsignal med samma nivåer och brus som appens simuleringsläge (0,2/4,8 V, 0,1 V brus)."""
    fore = round(VILOTID * sample_rate)
    start = fore + round(0.037 * sample_rate)
    langd = start + int(np.ceil(len(bitar) * step_time * sample_rate)) + round((VILOTID + MAX_FORDROJNING) * sample_rate)
    position = np.floor((np.arange(langd) - start) / (sample_rate * step_time)).astype(int)
    aktiv = (position >= 0) & (position < len(bitar))
    signal = np.full(langd, 0.2)
    signal[aktiv] += 4.6 * bitar[position[aktiv]]
    signal += np.random.default_rng().normal(0, 0.1, langd)
    return signal, sample_rate, step_time, fore


def kor_kalibrering(step_time, nuvarande_troskel, simulera, ai_kanal="Dev1/ai0", ao_kanal="Dev1/ao0"):
    """
    Skickar kalibreringens kända testmönster, analyserar mottagen signal och sparar rapporten.
    Returnerar rapporten (dict). Ändrar inga inställningar; det gör anroparen om rapporten är godkänd.
    """
    if not 0.001 <= step_time <= 0.1:
        raise ValueError("Kalibreringen kräver en bittid på 1–100 ms.")
    sample_rate = berakna_sampel_per_bit(step_time) / step_time
    bitar, antal_traning = training_pattern()

    if simulera:
        signal, sample_rate, dt, fore = _simulera_matning(bitar, step_time, sample_rate)
    else:
        signal, sample_rate, dt, fore = capture(
            bitar, ai_channel=ai_kanal, ao_channel=ao_kanal, step_time=step_time,
            sample_rate=sample_rate, idle=VILOTID, max_delay=MAX_FORDROJNING)

    rapport = analyse(signal, bitar, antal_traning, sample_rate=sample_rate, step_time=dt,
                      earliest_start=fore, max_delay=MAX_FORDROJNING, min_margin=MIN_MARGINAL,
                      current_threshold=nuvarande_troskel)
    nu = datetime.now().astimezone()
    rapport.update(measured_at=nu.isoformat(), date=nu.date().isoformat(),
                   simulated=simulera, ai_channel=ai_kanal, ao_channel=ao_kanal,
                   method_version=1, source="gränssnitt",
                   limitation='Känt testmönster; ordinarie startsynk och långa ramar är inte verifierade.')

    # Ny mapp per körning: tidigare mätningar skrivs aldrig över
    RESULTATMAPP.mkdir(parents=True, exist_ok=True)
    mapp = Path(tempfile.mkdtemp(prefix=nu.strftime('%Y%m%d-%H%M%S-'), dir=RESULTATMAPP))
    np.savez_compressed(mapp / 'matdata.npz', signal=signal, bits=bitar,
                        training_count=antal_traning, sample_rate=sample_rate, step_time=dt)
    (mapp / 'rapport.json').write_text(
        json.dumps(rapport, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    rapport["mapp"] = str(mapp)
    return rapport


def _tidpunkt(rapport):
    try:
        return datetime.fromisoformat(rapport["measured_at"]).timestamp()
    except Exception:
        return 0.0


def senaste_kalibrering():
    """
    Senaste godkända kalibreringen, eller None.
    En uppmätt kalibrering går alltid före en simulerad, så att en simulerad
    tröskel aldrig ersätter en riktig vid uppstart.
    """
    godkanda = []
    for fil in RESULTATMAPP.glob("*/rapport.json"):
        try:
            rapport = json.loads(fil.read_text(encoding="utf-8"))
        except Exception:
            continue
        if rapport.get("accepted") and rapport.get("recommended_threshold_v") is not None:
            rapport["mapp"] = str(fil.parent)
            godkanda.append(rapport)
    for simulerad in (False, True):
        kandidater = [r for r in godkanda if bool(r.get("simulated")) == simulerad]
        if kandidater:
            return max(kandidater, key=_tidpunkt)
    return None


def kalibreringstid(rapport):
    """Tidpunkten för kalibreringen som text, t.ex. '2026-11-03 14:05'."""
    try:
        return datetime.fromisoformat(rapport["measured_at"]).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return rapport.get("date", "okänt datum")
