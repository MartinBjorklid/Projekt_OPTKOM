"""Kalibrering av bittid (step_time) och tröskel (threshold).

Skickar testramar på Dev1/ao0 exakt som skicka.py gör och spelar samtidigt in
Dev1/ai0 med hårdvaruklocka. Inspelningarna avkodas med SynkadBitavkodare från
taemot.py, alltså samma kod (synk, klockföljning, längdfält) som vid riktig
mottagning.

1. Svep: för varje bitfrekvens, från långsam till snabb, skickas en kort
   testram ANTAL_UPPREPNINGAR gånger. En tröskel godkänns om ALLA bitar avkodas
   rätt i ALLA upprepningar och varje bitmedian ligger minst en brusmarginal
   från tröskeln.
2. Långt test: vid den snabbaste godkända frekvensen minus SAKERHETSMARGINAL
   skickas LANGT_TEST_BITAR bitar. Det avslöjar glidning och nivåer som
   driver under långa meddelanden. Misslyckas det provas nästa lägre frekvens.
3. Tröskeln sätts i MITTEN av intervallet som fungerar i både svepet och det
   långa testet, så att marginalen blir lika stor åt båda hållen.

Resultatet skrivs till parametrar.py som övriga program importerar.
Det långa testet sparas i kalibrering.npz och kan granskas med felsok.py.
"""

import datetime
from pathlib import Path

import numpy as np

import felsok
from skicka import bygg_ram, till_spanningar, ao_frekvens, AO_SAMPEL_PER_BIT
from taemot import samplingsparametrar, SynkadBitavkodare

AO_KANAL = "Dev1/ao0"
AI_KANAL = "Dev1/ai0"

# Bitfrekvenser att prova [Hz], från långsam till snabb (step_time = 1 / f).
FREKVENSER = list(range(200, 351, 10))
ANTAL_UPPREPNINGAR = 3   # korta mätningar per frekvens; alla måste lyckas
SAKERHETSMARGINAL = 0.10  # välj högst (1 - marginal) * snabbaste godkända frekvens
LANGT_TEST_BITAR = 3000  # längd på det långa testet vid vald frekvens
VILOTID = 0.3            # s med 0 V före testramen (mäter vilonivå/brus)
BRUSFAKTOR = 4.0         # marginal = max(BRUSFAKTOR * brus-std, MIN_MARGINAL)
MIN_MARGINAL = 0.05      # V
TROSKELSTEG = 0.02       # V, upplösning i tröskelsökningen

PARAMETERFIL = Path(__file__).with_name("parametrar.py")
TESTFIL = Path(__file__).with_name("kalibrering.npz")


def prbs7(antal=127):
    """Pseudoslumpföljd (x^7 + x^6 + 1) – innehåller alla 7-bitarsmönster utom 0000000."""
    register = 0b1111111
    bitar = []
    for _ in range(antal):
        ny = ((register >> 6) ^ (register >> 5)) & 1
        register = ((register << 1) | ny) & 0b1111111
        bitar.append(ny)
    return bitar


# Värstafall för LC-kristallen: ensam etta efter lång vila, ensam nolla efter
# långa ettor, snabb växling, samt en pseudoslumpföljd.
KORTA_TESTBITAR = ([0] * 8 + [1] + [0] * 8
                   + [1] * 8 + [0] + [1] * 8
                   + [1, 0] * 8
                   + prbs7()
                   + [0] * 4)
LANGA_TESTBITAR = np.random.default_rng(1).integers(0, 2, LANGT_TEST_BITAR).tolist()


def mat(step_time, nyttobitar):
    """Skicka en ram med nyttobitarna och returnera (signal, sampel_per_bit, AI-frekvens).

    sampel_per_bit räknas från de frekvenser DAQ:n faktiskt använder.
    """
    # Importeras här så att gränssnittets simulerade kalibrering fungerar utan nidaqmx
    import nidaqmx
    from nidaqmx.constants import AcquisitionType, TerminalConfiguration

    _, ai_rate = samplingsparametrar(step_time)
    ao_rate = ao_frekvens(step_time)
    vilosampel = [0.0] * int(np.ceil(VILOTID * ao_rate))
    spanningar = vilosampel + till_spanningar(bygg_ram(nyttobitar)) + vilosampel
    ao_tid = len(spanningar) / ao_rate
    antal_sampel = int((ao_tid + VILOTID) * ai_rate)

    try:
        with nidaqmx.Task() as ai, nidaqmx.Task() as ao:
            ai.ai_channels.add_ai_voltage_chan(
                AI_KANAL, terminal_config=TerminalConfiguration.RSE,
                min_val=-10.0, max_val=10.0,
            )
            ai.timing.cfg_samp_clk_timing(
                rate=ai_rate, sample_mode=AcquisitionType.FINITE,
                samps_per_chan=antal_sampel,
            )
            ao.ao_channels.add_ao_voltage_chan(AO_KANAL, min_val=-5.0, max_val=5.0)
            ao.timing.cfg_samp_clk_timing(
                rate=ao_rate, sample_mode=AcquisitionType.FINITE,
                samps_per_chan=len(spanningar),
            )
            ai_rate = float(ai.timing.samp_clk_rate)
            bitfrekvens = float(ao.timing.samp_clk_rate) / AO_SAMPEL_PER_BIT
            ao.write(spanningar, auto_start=False)

            # AI startas först så att vilonivån alltid finns i början av inspelningen.
            ai.start()
            ao.start()
            signal = ai.read(number_of_samples_per_channel=antal_sampel,
                             timeout=ao_tid + VILOTID + 5.0)
            ao.wait_until_done(timeout=5.0)
    finally:
        try:
            with nidaqmx.Task() as noll:
                noll.ao_channels.add_ao_voltage_chan(AO_KANAL, min_val=-5.0, max_val=5.0)
                noll.write(0.0)
        except Exception:
            pass

    return np.asarray(signal, dtype=float), ai_rate / bitfrekvens, ai_rate


def avkoda(signal, n, troskel):
    avkodare = SynkadBitavkodare(n, troskel, tyst=True)
    avkodare.mata_in(signal.tolist())
    return avkodare


def godkand(matning, troskel, marginal):
    """True om hela ramen avkodas rätt med minst `marginal` V till tröskeln."""
    signal, n, _, nyttobitar = matning
    avkodare = avkoda(signal, n, troskel)
    if not avkodare.klar:
        return False
    medianer = np.array(avkodare.medianer)
    bitar = medianer >= troskel
    if not np.array_equal(bitar, np.array(bygg_ram(nyttobitar), dtype=bool)):
        return False
    return (medianer[bitar].min() - troskel >= marginal
            and troskel - medianer[~bitar].max() >= marginal)


def godkanda_trosklar(matningar, vilonivå, hogsta, marginal):
    """Trösklar (på ett gemensamt galler) som fungerar i alla mätningar."""
    # Multipler av TROSKELSTEG, så att olika mätserier kan jämföras exakt.
    forsta = int(np.ceil((vilonivå + marginal) / TROSKELSTEG))
    sista = int(np.floor((hogsta - marginal) / TROSKELSTEG))
    trosklar = [round(k * TROSKELSTEG, 6) for k in range(forsta, sista + 1)]
    return [t for t in trosklar if all(godkand(m, t, marginal) for m in matningar)]


def gor_matningar(step_time, nyttobitar, antal):
    """Returnera (mätningar, vilonivå, högsta värde, marginal)."""
    matningar = []
    vilonivaer, brus = [], []
    for _ in range(antal):
        signal, n, ai_rate = mat(step_time, nyttobitar)
        vila = signal[:int(0.8 * VILOTID * ai_rate)]
        vilonivaer.append(np.median(vila))
        brus.append(np.std(vila))
        matningar.append((signal, n, ai_rate, nyttobitar))
    hogsta = max(m[0].max() for m in matningar)
    marginal = max(MIN_MARGINAL, BRUSFAKTOR * max(brus))
    return matningar, max(vilonivaer), hogsta, marginal


def svep(frekvens):
    """Korta tester vid en frekvens. Returnerar ett resultat-dict."""
    matningar, vilonivå, hogsta, marginal = gor_matningar(
        1 / frekvens, KORTA_TESTBITAR, ANTAL_UPPREPNINGAR)
    giltiga = godkanda_trosklar(matningar, vilonivå, hogsta, marginal)
    return {"frekvens": frekvens, "giltiga": giltiga, "vilonivå": vilonivå,
            "hogsta": hogsta, "marginal": marginal}


def langt_test(resultat):
    """Långt test vid resultatets frekvens. Uppdaterar resultatet; True om det klarades."""
    frekvens = resultat["frekvens"]
    print(f"\nLångt test vid {frekvens} Hz ({LANGT_TEST_BITAR} bitar, "
          f"{LANGT_TEST_BITAR / frekvens:.0f} s)...")
    matningar, vilonivå, hogsta, marginal = gor_matningar(1 / frekvens, LANGA_TESTBITAR, 1)
    marginal = max(marginal, resultat["marginal"])
    giltiga_langt = godkanda_trosklar(matningar, vilonivå, hogsta, marginal)
    gemensamma = sorted(set(resultat["giltiga"]) & set(giltiga_langt))

    signal, n, ai_rate, _ = matningar[0]
    # Tröskel för felsökningsdatan: mitten om testet lyckades, annars sveptes mitt.
    giltiga = gemensamma or resultat["giltiga"]
    troskel = round((min(giltiga) + max(giltiga)) / 2, 3)
    felsok.spara(TESTFIL, avkoda(signal, n, troskel), ai_rate, bygg_ram(LANGA_TESTBITAR))

    if not gemensamma:
        print(f"{frekvens} Hz: långa testet MISSLYCKADES – ingen tröskel klarar hela "
              f"meddelandet. (Granska med: python felsok.py {TESTFIL.name})")
        return False
    resultat.update(troskel_min=min(gemensamma), troskel_max=max(gemensamma),
                    threshold=troskel, marginal=marginal,
                    hogsta=max(hogsta, resultat["hogsta"]))
    print(f"{frekvens} Hz: långa testet OK, trösklar {min(gemensamma):.3f}–"
          f"{max(gemensamma):.3f} V")
    return True


def spara(resultat):
    tid = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    PARAMETERFIL.write_text(
        '"""Gemensamma länkparametrar. Importeras av skicka.py, taemot.py och program.py.\n'
        '\n'
        'Skrivs över av kalibrering.py men kan också ändras för hand.\n'
        '"""\n'
        '\n'
        f'# Kalibrerad {tid}: vilonivå {resultat["vilonivå"]:.3f} V, '
        f'högsta uppmätta {resultat["hogsta"]:.3f} V,\n'
        f'# godkända trösklar {resultat["troskel_min"]:.3f}–{resultat["troskel_max"]:.3f} V '
        f'(marginal {resultat["marginal"]:.3f} V), klarade {LANGT_TEST_BITAR} bitar.\n'
        f'step_time = 1 / {resultat["frekvens"]}  # sekunder per bit ({resultat["frekvens"]} Hz)\n'
        f'threshold = {resultat["threshold"]!r}  # V; AI-sampel >= threshold tolkas som 1\n',
        encoding="utf-8",
    )
    print(f"Sparat i {PARAMETERFIL}")


def main():
    print(f"Svep: {len(bygg_ram(KORTA_TESTBITAR))} bitar per ram, "
          f"{ANTAL_UPPREPNINGAR} mätningar per frekvens.\n")
    godkanda = []
    for frekvens in FREKVENSER:
        r = svep(frekvens)
        if r["giltiga"]:
            print(f"{frekvens:4d} Hz: OK   trösklar {min(r['giltiga']):.3f}–"
                  f"{max(r['giltiga']):.3f} V (vila {r['vilonivå']:.3f} V, "
                  f"max {r['hogsta']:.3f} V)")
            godkanda.append(r)
        else:
            print(f"{frekvens:4d} Hz: FEL  ingen tröskel fungerar "
                  f"(vila {r['vilonivå']:.3f} V, max {r['hogsta']:.3f} V)")
            # Sluta vid första misslyckandet efter godkända frekvenser.
            if godkanda:
                break

    if not godkanda:
        print("\nIngen frekvens fungerade. Kontrollera kopplingen, ljuskällan och att en "
              "etta ger HÖGRE spänning på ai0 än vila (avkodaren kräver det).")
        return

    snabbaste = godkanda[-1]["frekvens"]
    tak = (1 - SAKERHETSMARGINAL) * snabbaste
    print(f"\nSnabbaste godkända frekvens i svepet: {snabbaste} Hz")

    # Långt test från (1 - marginal) * snabbaste och nedåt tills ett klaras.
    kandidater = sorted((r for r in godkanda if r["frekvens"] <= tak),
                        key=lambda r: -r["frekvens"]) or [godkanda[0]]
    vald = next((r for r in kandidater if langt_test(r)), None)
    if vald is None:
        print("\nInget långt test klarades. Granska kalibrering.npz med felsok.py.")
        felsok.plotta(TESTFIL)
        return

    print(f"\nVald frekvens: {vald['frekvens']} Hz, step_time = "
          f"{1000 / vald['frekvens']:.3f} ms")
    print(f"Vald threshold: {vald['threshold']} V (mitt i {vald['troskel_min']:.3f}–"
          f"{vald['troskel_max']:.3f} V)")

    if input("\nSpara till parametrar.py? [j/n] ").strip().lower().startswith("j"):
        spara(vald)
    felsok.plotta(TESTFIL)


if __name__ == "__main__":
    main()
