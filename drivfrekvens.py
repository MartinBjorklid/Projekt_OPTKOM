"""Bestämmer optimal drivfrekvens och LC-modulatorns maximala hastighet.

Drivfrekvensen är frekvensen på växelspänningen +/- drivamplitud (parametrar.py)
som ligger över kristallen under en etta (kristallen är på oavsett tecken). Maximal hastighet är
hur många bitar per sekund kristallen kan slås av och på i takt med datan.

Steg 1 – drivfrekvens. Ett antal drivfrekvenser provas, och för var och en
mäts på ai0:
  * kontrast           – stabil på-nivå (median) minus vilonivå,
  * rippel             – hur mycket ljuset fladdrar i takt med polaritetsbytena,
  * effektiv kontrast  – LÄGSTA stabila på-nivå minus vilonivå, dvs. kontrasten
                         som avkodaren ser i värsta fall (väger ihop båda ovan),
  * stigtid / falltid  – 10 -> 90 % vid påslag, 90 -> 10 % vid avslag.
För låg frekvens hinner kristallen delvis följa varje polaritetsbyte (rippel).
För hög frekvens hinner AO-utgången inte ladda om kristallens kapacitans, så
effektivspänningen sjunker: lägre kontrast och långsammare påslag.
Optimal drivfrekvens = den HÖGSTA frekvens som har minst OPTIMAL_KONTRAST av
bästa effektiva kontrast och en stig- + falltid högst OMSLAGSTOLERANS sämre än
den bästa (högre frekvens ger mindre rippel; gränserna ser till att kontrast
och omslagstid inte offras för det).

Steg 2 – maximal hastighet. Med den optimala drivfrekvensen skickas ett
testmönster (ensam etta bland nollor, ensam nolla bland ettor, 1010...) i allt
högre bithastighet. För varje bithastighet mäts ögonöppningen: svagaste ettans
bitmedian minus starkaste nollans, med bästa möjliga samplingsfas. Den beror
inte på någon tröskel.
Maximal hastighet = högsta bithastighet där ögonöppningen är minst OGONGRANS av
full kontrast. Där ögonöppningen blir noll eller negativ går 0 och 1 inte
längre att skilja åt; det syns i nivå- och ögondiagrammen som visas sist.

Den optimala drivfrekvensen kan sparas i parametrar.py. Kör kalibrering.py
efteråt; den väljer den bithastighet länken faktiskt ska använda.
"""

import datetime

import numpy as np
import nidaqmx
from nidaqmx.constants import AcquisitionType, TerminalConfiguration

from kalibrering import uppdatera_parametrar, prbs7
from skicka import (AO_MAX_FREKVENS, AO_MAX_SPANNING, ao_sampel_per_bit, till_spanningar,
                    drivamplitud)

AO_KANAL = "Dev1/ao0"
AI_KANAL = "Dev1/ai0"
AI_RATE = 50_000           # S/s; tillräckligt för att se rippel upp till några kHz
VILOTID = 0.3              # s med 0 V i början av varje mätning (vilonivå/brus)

# --- Steg 1: drivfrekvens -------------------------------------------------
DRIV_AO_RATE = AO_MAX_FREKVENS  # S/s; drivfrekvens = DRIV_AO_RATE / (2 * halvperiod)
# Halvperiod i AO-sampel. Vid 5000 S/s: 2500, 1250, 833, 625, 500, 417, 312,
# 250, 192, 147, 100, 50 och 25 Hz.
HALVPERIODER = [1, 2, 3, 4, 5, 6, 8, 10, 13, 17, 25, 50, 100]
ANTAL_UPPREPNINGAR = 2
LANG_PA = 0.3     # s påslag för att mäta stabil nivå och rippel
PULS_PA = 0.02    # s per kort puls (stigtid)
PULS_AV = 0.03    # s mellan pulserna (falltid; kristallen slappnar av långsamt)
ANTAL_PULSER = 5
UTJAMNING = 0.0005  # s glidande medelvärde innan stig- och falltid mäts
OPTIMAL_KONTRAST = 0.95   # effektiv kontrast >= 95 % av bästa ...
OMSLAGSTOLERANS = 0.10    # ... och stig- + falltid högst 10 % sämre än bästa

# --- Steg 2: maximal hastighet --------------------------------------------
BITHASTIGHETER = [100, 150, 200, 250, 300, 350, 400, 450, 500, 600, 700, 800, 1000,
                  1200, 1500, 2000]
OGONGRANS = 0.25   # maximal hastighet: ögonöppning >= 25 % av full kontrast
MONSTER = ([0] * 8 + [1] + [0] * 8
           + [1] * 8 + [0] + [1] * 8
           + [1, 0] * 16
           + prbs7())
REFERENS_PA = 0.03  # s med ettor före mönstret (full nivå och tidsreferens)


def mat(spanningar, ao_rate):
    """Skicka AO-spänningarna och spela in ai0. Returnerar (signal, AI-, AO-frekvens)."""
    ao_tid = len(spanningar) / ao_rate
    antal_sampel = int((ao_tid + 0.2) * AI_RATE)

    try:
        with nidaqmx.Task() as ai, nidaqmx.Task() as ao:
            ai.ai_channels.add_ai_voltage_chan(
                AI_KANAL, terminal_config=TerminalConfiguration.RSE,
                min_val=-10.0, max_val=10.0,
            )
            ai.timing.cfg_samp_clk_timing(
                rate=AI_RATE, sample_mode=AcquisitionType.FINITE,
                samps_per_chan=antal_sampel,
            )
            ao.ao_channels.add_ao_voltage_chan(AO_KANAL, min_val=-AO_MAX_SPANNING, max_val=AO_MAX_SPANNING)
            ao.timing.cfg_samp_clk_timing(
                rate=ao_rate, sample_mode=AcquisitionType.FINITE,
                samps_per_chan=len(spanningar),
            )
            ai_rate = float(ai.timing.samp_clk_rate)
            ao_rate = float(ao.timing.samp_clk_rate)
            ao.write(spanningar, auto_start=False)

            # AI startas först så att vilonivån alltid finns i början av inspelningen.
            ai.start()
            ao.start()
            signal = ai.read(number_of_samples_per_channel=antal_sampel,
                             timeout=ao_tid + 5.0)
            ao.wait_until_done(timeout=5.0)
    finally:
        try:
            with nidaqmx.Task() as noll:
                noll.ao_channels.add_ao_voltage_chan(AO_KANAL, min_val=-AO_MAX_SPANNING, max_val=AO_MAX_SPANNING)
                noll.write(0.0)
        except Exception:
            pass

    return np.asarray(signal, dtype=float), ai_rate, ao_rate


def forsta_index(villkor, fran):
    """Första index >= fran där villkor är sant, annars None."""
    fran = max(0, fran)
    traffar = np.flatnonzero(villkor[fran:])
    return fran + int(traffar[0]) if traffar.size else None


def vilonivå(signal, ai_rate):
    vila = signal[:int(0.8 * VILOTID * ai_rate)]
    return float(np.median(vila)), float(np.std(vila))


# ==========================================================================
# Steg 1: drivfrekvens
# ==========================================================================

def drivsignal(halvperiod, amplitud=drivamplitud, ao_rate=DRIV_AO_RATE):
    """Returnera (AO-spänningar, lista med (start, slut) i AO-sampel för varje påslag)."""
    spanningar, paslag = [], []

    def av(sekunder):
        spanningar.extend([0.0] * int(round(sekunder * ao_rate)))

    def pa(sekunder):
        start = len(spanningar)
        spanningar.extend(amplitud if (k // halvperiod) % 2 == 0 else -amplitud
                          for k in range(int(round(sekunder * ao_rate))))
        paslag.append((start, len(spanningar)))

    av(VILOTID)
    pa(LANG_PA)
    av(VILOTID)
    for _ in range(ANTAL_PULSER):
        pa(PULS_PA)
        av(PULS_AV)
    av(0.1)
    return spanningar, paslag


def analysera_drivning(signal, ai_rate, paslag):
    """Returnera dict med kontrast, rippel, effektiv kontrast, stig- och falltid."""
    vila, brus = vilonivå(signal, ai_rate)

    # Stabil nivå och rippel: andra halvan av det långa påslaget.
    start, slut = paslag[0]
    stabil = signal[int((start + 0.5 * (slut - start)) * ai_rate):int((slut - 0.01) * ai_rate)]
    kontrast = float(np.median(stabil)) - vila
    # Toppvärde till toppvärde (5–95 %), minus bidraget från vanligt brus.
    rippel = max(0.0, float(np.percentile(stabil, 95) - np.percentile(stabil, 5)) - 3.3 * brus)
    # Lägsta stabila nivå: 5:e percentilen, där brusets bidrag (-1,65 std) räknats bort.
    effektiv = float(np.percentile(stabil, 5)) + 1.65 * brus - vila
    resultat = {"kontrast": kontrast, "rippel": rippel, "effektiv": effektiv,
                "stigtid": np.nan, "falltid": np.nan}
    if kontrast < 10 * brus:
        return resultat  # kristallen slår inte om alls

    k = max(1, int(UTJAMNING * ai_rate))
    glatt = np.convolve(signal, np.ones(k) / k, mode="same")
    niva10, niva50, niva90 = (vila + p * kontrast for p in (0.1, 0.5, 0.9))

    # Fördröjning mellan AO-start och AI (plus kristallens halva stigtid), från långa påslaget.
    i50 = forsta_index(glatt >= niva50, int((start - 0.05) * ai_rate))
    fordrojning = (i50 / ai_rate - start) if i50 is not None else 0.0

    stig, fall = [], []
    for nr, (start, slut) in enumerate(paslag[1:], start=1):
        nasta = paslag[nr + 1][0] if nr + 1 < len(paslag) else slut + PULS_AV
        i10 = forsta_index(glatt >= niva10, int((start + fordrojning - 0.01) * ai_rate))
        i90 = forsta_index(glatt >= niva90, i10) if i10 is not None else None
        if i90 is not None and i90 / ai_rate < slut + fordrojning:
            stig.append((i90 - i10) / ai_rate)
        j90 = forsta_index(glatt < niva90, int((slut + fordrojning - 0.005) * ai_rate))
        j10 = forsta_index(glatt <= niva10, j90) if j90 is not None else None
        if j10 is not None and j10 / ai_rate < nasta + fordrojning:
            fall.append((j10 - j90) / ai_rate)

    if stig:
        resultat["stigtid"] = float(np.median(stig))
    if fall:
        resultat["falltid"] = float(np.median(fall))
    return resultat


def mat_drivfrekvens(halvperiod, amplitud=drivamplitud, ao_rate=DRIV_AO_RATE):
    """Mät ANTAL_UPPREPNINGAR gånger; returnera medianen av varje storhet."""
    spanningar, paslag = drivsignal(halvperiod, amplitud, ao_rate)
    nominell = ao_rate
    matningar = []
    for _ in range(ANTAL_UPPREPNINGAR):
        signal, ai_rate, ao_rate = mat(spanningar, nominell)
        paslag_s = [(a / ao_rate, b / ao_rate) for a, b in paslag]
        matningar.append(analysera_drivning(signal, ai_rate, paslag_s))
    r = {nyckel: float(np.median([m[nyckel] for m in matningar])) for nyckel in matningar[0]}
    r["frekvens"] = nominell / (2 * halvperiod)
    return r


def valj_drivfrekvens(resultat):
    basta_effektiv = max(r["effektiv"] for r in resultat)
    kandidater = [r for r in resultat
                  if r["effektiv"] >= OPTIMAL_KONTRAST * basta_effektiv
                  and np.isfinite(r["stigtid"]) and np.isfinite(r["falltid"])]
    if not kandidater:
        return max(resultat, key=lambda r: r["effektiv"])["frekvens"]
    snabbast = min(r["stigtid"] + r["falltid"] for r in kandidater)
    return max(r["frekvens"] for r in kandidater
               if r["stigtid"] + r["falltid"] <= (1 + OMSLAGSTOLERANS) * snabbast)


# ==========================================================================
# Steg 2: maximal hastighet
# ==========================================================================

def modulationssignal(bithastighet, drivfrekvens, amplitud=drivamplitud):
    """AO-spänningar: vila, REFERENS_PA ettor, paus, testmönstret, vila.

    Returnerar (spänningar, AO-sampel per bit, start för referens resp. mönster i bitar).
    """
    bittid = 1 / bithastighet
    sampel = ao_sampel_per_bit(bittid, drivfrekvens)
    vila = [0] * int(np.ceil(VILOTID * bithastighet))
    referens = [1] * int(np.ceil(REFERENS_PA * bithastighet))
    paus = [0] * int(np.ceil(REFERENS_PA * bithastighet))
    bitar = vila + referens + paus + MONSTER + [0] * int(np.ceil(0.1 * bithastighet))
    return (till_spanningar(bitar, bittid, drivfrekvens, amplitud), sampel,
            len(vila), len(vila) + len(referens) + len(paus))


def analysera_modulation(signal, ai_rate, bitfrekvens, referensstart, monsterstart):
    """Returnera dict med full kontrast, ögonöppning (V och andel) och bästa fas."""
    vila, _ = vilonivå(signal, ai_rate)
    n = ai_rate / bitfrekvens
    ref_a = referensstart * n
    ref_langd = int(np.ceil(REFERENS_PA * bitfrekvens)) * n
    pa_niva = float(np.median(signal[int(ref_a + 0.5 * ref_langd):int(ref_a + 0.95 * ref_langd)]))
    kontrast = pa_niva - vila

    # AI/AO-förskjutning: där referensens påslag passerar 10 %.
    i10 = forsta_index(signal >= vila + 0.1 * kontrast, int(ref_a - 0.02 * ai_rate))
    forskjutning = (i10 - ref_a) if i10 is not None else 0.0

    # Läs varje bit som avkodaren gör (median av mittersta tredjedelen) och
    # prova olika samplingsfaser; den bästa motsvarar en väl synkad mottagare.
    monster = np.array(MONSTER, dtype=bool)
    starter = monsterstart * n + forskjutning + np.arange(len(MONSTER)) * n
    basta = (-np.inf, 0.0, None)
    for fas in np.linspace(-0.25, 0.75, 21):
        a = np.round(starter + (fas + 1 / 3) * n).astype(int)
        b = np.round(starter + (fas + 2 / 3) * n).astype(int)
        if a[0] < 0 or b[-1] > len(signal):
            continue
        medianer = np.array([np.median(signal[x:y]) for x, y in zip(a, b)])
        ogon = float(medianer[monster].min() - medianer[~monster].max())
        if ogon > basta[0]:
            basta = (ogon, fas, medianer)
    ogon, fas, medianer = basta
    return {"kontrast": kontrast, "vila": vila, "ogon": ogon,
            "andel": ogon / kontrast if kontrast > 0 else -np.inf,
            "medianer": medianer, "signal": signal, "n": n,
            # Start för bit 0 i mönstret, förskjuten till bästa samplingsfas.
            "monsterstart": monsterstart * n + forskjutning + fas * n}


def mat_bithastighet(bithastighet, drivfrekvens, amplitud=drivamplitud):
    spanningar, sampel, referensstart, monsterstart = modulationssignal(
        bithastighet, drivfrekvens, amplitud)
    signal, ai_rate, ao_rate = mat(spanningar, sampel * bithastighet)
    r = analysera_modulation(signal, ai_rate, ao_rate / sampel, referensstart, monsterstart)
    r["bithastighet"] = bithastighet
    r["drivning"] = sampel * bithastighet / 2
    return r


# ==========================================================================

def ms(sekunder):
    return f"{sekunder * 1000:6.2f}" if np.isfinite(sekunder) else "     –"


def plotta(driv, optimal, modulation, maximal):
    import matplotlib.pyplot as plt

    fig, axlar = plt.subplots(2, 2, figsize=(13, 8))
    ax1, ax2, ax3, ax4 = axlar.ravel()

    f = [r["frekvens"] for r in driv]
    ax1.semilogx(f, [r["kontrast"] for r in driv], "o-", label="kontrast")
    ax1.semilogx(f, [r["effektiv"] for r in driv], "^-", label="effektiv kontrast")
    ax1.semilogx(f, [r["rippel"] for r in driv], "s-", label="rippel (topp-topp)")
    ax1.set_ylabel("Spänning [V]")
    ax1.set_title("Steg 1: kontrast och rippel mot drivfrekvens")
    ax2.semilogx(f, [r["stigtid"] * 1000 for r in driv], "o-", label="stigtid 10–90 %")
    ax2.semilogx(f, [r["falltid"] * 1000 for r in driv], "s-", label="falltid 90–10 %")
    ax2.set_ylabel("Tid [ms]")
    ax2.set_title("Steg 1: omslagstider mot drivfrekvens")
    for ax in (ax1, ax2):
        ax.axvline(optimal, color="green", linestyle="--", label=f"optimal {optimal:.0f} Hz")
        ax.set_xlabel("Drivfrekvens [Hz]")
        ax.grid(True, which="both")
        ax.legend()

    ax3.plot([r["bithastighet"] for r in modulation],
             [100 * r["andel"] for r in modulation], "o-")
    ax3.axhline(100 * OGONGRANS, color="red", linestyle=":", label=f"gräns {OGONGRANS:.0%}")
    if maximal:
        ax3.axvline(maximal, color="green", linestyle="--", label=f"maximal {maximal} bit/s")
    ax3.set_xlabel("Bithastighet [bit/s]")
    ax3.set_ylabel("Ögonöppning [% av full kontrast]")
    ax3.set_title(f"Steg 2: LC-modulatorns hastighet (drivfrekvens {optimal:.0f} Hz)")
    ax3.grid(True)
    ax3.legend()

    # Testmönstret vid maximal hastighet och nästa (för snabba) hastighet.
    for r in modulation:
        if maximal is not None and r["bithastighet"] < maximal:
            continue
        a = int(r["monsterstart"])
        b = int(a + len(MONSTER) * r["n"])
        ax4.plot((np.arange(a, b) - a) / r["n"], r["signal"][a:b], linewidth=0.8,
                 label=f"{r['bithastighet']} bit/s")
        if maximal is None or r["bithastighet"] > maximal:
            break
    ax4.set_xlabel("Bitnummer i testmönstret")
    ax4.set_ylabel("ai0 [V]")
    ax4.set_title("Testmönstret vid maximal och nästa hastighet")
    ax4.grid(True)
    ax4.legend()

    plt.tight_layout()
    plotta_hastighet(modulation, maximal)
    plt.show()


def plotta_hastighet(modulation, maximal):
    """Nivådiagram över alla bithastigheter och ögondiagram för några av dem."""
    import matplotlib.pyplot as plt

    monster = np.array(MONSTER, dtype=bool)
    hastigheter = [r["bithastighet"] for r in modulation]
    stangd = next((r["bithastighet"] for r in modulation if r["ogon"] <= 0), None)

    fig = plt.figure(figsize=(14, 9))
    rutnat = fig.add_gridspec(2, 4, height_ratios=[1.2, 1])
    ax = fig.add_subplot(rutnat[0, :])

    # Varje bits median, ettor och nollor för sig. Jämnt fördelade i x inom
    # varje hastighet så att punkterna inte hamnar ovanpå varandra.
    for i, r in enumerate(modulation):
        if r["medianer"] is None:
            continue
        sprid = np.linspace(-0.3, 0.3, len(MONSTER))
        ax.scatter(i + sprid[monster], r["medianer"][monster], s=5, color="tab:blue",
                   alpha=0.6, label="ettor" if i == 0 else None)
        ax.scatter(i + sprid[~monster], r["medianer"][~monster], s=5, color="tab:orange",
                   alpha=0.6, label="nollor" if i == 0 else None)
    x = np.arange(len(modulation))
    svagaste_etta = [r["medianer"][monster].min() if r["medianer"] is not None else np.nan
                     for r in modulation]
    starkaste_nolla = [r["medianer"][~monster].max() if r["medianer"] is not None else np.nan
                       for r in modulation]
    ax.plot(x, svagaste_etta, "-", color="tab:blue", label="svagaste etta")
    ax.plot(x, starkaste_nolla, "-", color="tab:orange", label="starkaste nolla")
    ax.fill_between(x, svagaste_etta, starkaste_nolla,
                    where=np.array(svagaste_etta) > np.array(starkaste_nolla),
                    color="green", alpha=0.15, label="öppet öga (0 och 1 går att skilja)")
    if stangd is not None:
        ax.axvspan(hastigheter.index(stangd) - 0.5, len(modulation) - 0.5, color="red",
                   alpha=0.12, label=f"stängt öga från {stangd} bit/s")
    if maximal is not None:
        ax.axvline(hastigheter.index(maximal), color="green", linestyle="--",
                   label=f"maximal ({OGONGRANS:.0%} öppet) {maximal} bit/s")
    ax.set_xticks(x)
    ax.set_xticklabels([str(h) for h in hastigheter])
    ax.set_xlabel("Bithastighet [bit/s]")
    ax.set_ylabel("Bitmedian [V]")
    ax.set_title("Nivåer för varje etta och nolla i testmönstret – där färgerna "
                 "överlappar går 0 och 1 inte att skilja")
    ax.grid(True)
    ax.legend(loc="center left", fontsize=8)

    # Ögondiagram: långsammast, maximal, första stängda och snabbast.
    valda = [hastigheter[0], maximal, stangd, hastigheter[-1]]
    valda = list(dict.fromkeys(h for h in valda if h is not None))
    for kol, hastighet in enumerate(valda[:4]):
        r = modulation[hastigheter.index(hastighet)]
        axe = fig.add_subplot(rutnat[1, kol])
        n = r["n"]
        t = np.arange(int(2 * n)) / n - 0.5
        for k in range(1, len(MONSTER) - 1):
            a = int(round(r["monsterstart"] + (k - 0.5) * n))
            bit = r["signal"][a:a + len(t)]
            if len(bit) == len(t):
                axe.plot(t, bit, color="tab:blue" if MONSTER[k] else "tab:orange",
                         linewidth=0.4, alpha=0.35)
        axe.axvspan(1 / 3, 2 / 3, color="gray", alpha=0.2)
        andel = f"{100 * r['andel']:.0f} %" if np.isfinite(r["andel"]) else "–"
        axe.set_title(f"{hastighet} bit/s – ögonöppning {andel}", fontsize=10)
        axe.set_xlabel("Tid [bitar]")
        if kol == 0:
            axe.set_ylabel("ai0 [V]")
        axe.grid(True)
    fig.text(0.5, 0.005, "Ögondiagram: alla bitar ovanpå varandra (blå = etta, orange = nolla). "
             "Grått = där avkodaren läser biten. Öppet öga = tydligt glapp mellan färgerna där.",
             ha="center", fontsize=9)
    fig.tight_layout(rect=(0, 0.02, 1, 1))


def main():
    print(f"Steg 1: provar {len(HALVPERIODER)} drivfrekvenser, "
          f"{ANTAL_UPPREPNINGAR} mätningar var.\n")
    print("  Drivfrekvens   Kontrast   Rippel   Effektiv   Stigtid   Falltid")
    print("          [Hz]        [V]      [V]        [V]      [ms]      [ms]")
    driv = []
    for halvperiod in sorted(HALVPERIODER, reverse=True):
        r = mat_drivfrekvens(halvperiod)
        driv.append(r)
        print(f"  {r['frekvens']:12.0f}   {r['kontrast']:8.3f}   {r['rippel']:6.3f}"
              f"   {r['effektiv']:8.3f}    {ms(r['stigtid'])}    {ms(r['falltid'])}")

    if max(r["kontrast"] for r in driv) <= 0:
        print("\nIngen frekvens gav någon kontrast. Kontrollera kopplingen och ljuskällan.")
        return
    optimal = valj_drivfrekvens(driv)
    print(f"\nOptimal drivfrekvens: {optimal:.0f} Hz (högsta med minst {OPTIMAL_KONTRAST:.0%} "
          f"av bästa effektiva kontrast och nästan snabbaste omslag)")

    print(f"\nSteg 2: LC-modulatorns hastighet med drivfrekvens {optimal:.0f} Hz, "
          f"testmönster {len(MONSTER)} bitar.")
    print("(Drivningen kan bara bli ett heltal halvperioder per bit, därav kolumnen Drivning.)\n")
    print("  Bithastighet   Drivning   Kontrast   Ögonöppning")
    print("       [bit/s]       [Hz]        [V]       [V]   [%]")
    modulation = []
    for bithastighet in BITHASTIGHETER:
        r = mat_bithastighet(bithastighet, optimal)
        modulation.append(r)
        print(f"  {bithastighet:12d}   {r['drivning']:8.0f}   {r['kontrast']:8.3f}"
              f"   {r['ogon']:7.3f}  {100 * r['andel']:4.0f}")

    godkanda = [r["bithastighet"] for r in modulation if r["andel"] >= OGONGRANS]
    maximal = max(godkanda) if godkanda else None
    stangd = next((r["bithastighet"] for r in modulation if r["ogon"] <= 0), None)
    if stangd is not None:
        print(f"\nÖgat stängs vid {stangd} bit/s: där går 0 och 1 inte längre att skilja åt.")
    else:
        print(f"\nÖgat var fortfarande öppet vid {BITHASTIGHETER[-1]} bit/s.")
    if maximal is None:
        print(f"\nIngen bithastighet gav en ögonöppning på minst {OGONGRANS:.0%}.")
    else:
        print(f"\nLC-modulatorns maximala hastighet: {maximal} bit/s "
              f"(ögonöppning minst {OGONGRANS:.0%} av full kontrast)")
        if maximal == BITHASTIGHETER[-1]:
            print("Det var den högsta som provades – lägg till högre värden i BITHASTIGHETER.")
        print("kalibrering.py väljer sedan den bithastighet länken ska använda; den kan inte "
              "bli högre än så här.")

    if input("\nSpara drivfrekvensen till parametrar.py? [j/n] ").strip().lower().startswith("j"):
        tid = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        uppdatera_parametrar(
            drivfrekvens=f"drivfrekvens = {round(optimal)}  # Hz; växelspänningen som driver "
                         f"LC-kristallen under en etta; mätt {tid}"
                         + (f", LC-modulatorn klarar max {maximal} bit/s" if maximal else ""),
        )
        print("Kör kalibrering.py igen – bästa bithastighet och tröskel kan ha ändrats.")
    plotta(driv, optimal, modulation, maximal)


if __name__ == "__main__":
    main()
