"""Bestämmer optimal amplitud för LC-kristallens drivsignal.

Amplituden är toppvärdet på växelspänningen +/- A som ligger över kristallen
under en etta. Kristallen reagerar på spänningens kvadrat (RMS-värdet):
  * under en tröskelspänning slår den inte om alls,
  * ovanför tröskeln ökar kontrasten och påslaget går snabbare
    (påslagstiden ~ 1 / (A² - V_tröskel²)),
  * till slut mättas kontrasten och högre spänning ger inget mer.
Avslaget är elastisk avslappning och påverkas knappt av amplituden.

För varje amplitud i AMPLITUDER mäts, med drivningen exakt som vid sändning
(drivfrekvens och step_time i parametrar.py):
  * kontrast, rippel, effektiv kontrast, stig- och falltid (som drivfrekvens.py),
  * ögonöppningen vid den nuvarande bithastigheten (step_time i parametrar.py),
    det vill säga hur väl ettor och nollor går att skilja åt i ett testmönster.

Tröskelspänning   = lägsta amplitud där kontrasten är minst 10 % av den största.
Optimal amplitud  = LÄGSTA amplitud som ger minst OPTIMAL_ANDEL av både bästa
                    effektiva kontrast och bästa ögonöppning. Ovanför den vinns
                    nästan inget, medan kristallen belastas mer.

Resultatet kan sparas som drivamplitud i parametrar.py. Kör sedan
kalibrering.py igen, eftersom tröskeln och bästa bithastighet kan ha ändrats.
"""

import datetime

from drivfrekvens import mat_drivfrekvens, mat_bithastighet, ms
from kalibrering import uppdatera_parametrar
from parametrar import step_time, drivfrekvens
from skicka import AO_MAX_SPANNING, ao_sampel_per_bit

# Amplituder att prova [V]. USB-6003 klarar ±10 V. Kontrollera att er
# LC-kristall tål den högsta innan ni kör.
AMPLITUDER = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0]
OPTIMAL_ANDEL = 0.95   # optimal: minst 95 % av bästa effektiva kontrast och ögonöppning
TROSKELANDEL = 0.10    # tröskelspänning: kontrast minst 10 % av den största


def mat_amplitud(amplitud, ao_rate, bithastighet):
    """Mät drivegenskaper och ögonöppning vid en amplitud."""
    # Polariteten byts varje AO-sampel, precis som när skicka.py sänder.
    r = mat_drivfrekvens(1, amplitud, ao_rate)
    oga = mat_bithastighet(bithastighet, drivfrekvens, amplitud)
    r.update(amplitud=amplitud, ogon=oga["ogon"])
    return r


def valj(resultat):
    """Returnera (tröskelspänning, optimal amplitud) i V."""
    storsta = max(r["kontrast"] for r in resultat)
    troskel = min((r["amplitud"] for r in resultat
                   if r["kontrast"] >= TROSKELANDEL * storsta), default=None)
    basta_effektiv = max(r["effektiv"] for r in resultat)
    basta_ogon = max(r["ogon"] for r in resultat)
    kandidater = [r["amplitud"] for r in resultat
                  if r["effektiv"] >= OPTIMAL_ANDEL * basta_effektiv
                  and r["ogon"] >= OPTIMAL_ANDEL * basta_ogon]
    if kandidater:
        return troskel, min(kandidater)
    return troskel, max(resultat, key=lambda r: r["ogon"])["amplitud"]


def plotta(resultat, troskel, optimal, bithastighet):
    import matplotlib.pyplot as plt

    a = [r["amplitud"] for r in resultat]
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(15, 4.8))

    ax1.plot(a, [r["kontrast"] for r in resultat], "o-", label="kontrast")
    ax1.plot(a, [r["effektiv"] for r in resultat], "^-", label="effektiv kontrast")
    ax1.plot(a, [r["rippel"] for r in resultat], "s-", label="rippel (topp-topp)")
    ax1.set_ylabel("Spänning på ai0 [V]")
    ax1.set_title("Kontrast och rippel")

    ax2.plot(a, [r["stigtid"] * 1000 for r in resultat], "o-", label="stigtid 10–90 %")
    ax2.plot(a, [r["falltid"] * 1000 for r in resultat], "s-", label="falltid 90–10 %")
    ax2.set_ylabel("Tid [ms]")
    ax2.set_title("Omslagstider")

    ax3.plot(a, [r["ogon"] for r in resultat], "o-", label="ögonöppning")
    ax3.axhline(0, color="black", linewidth=0.8)
    ax3.set_ylabel("Svagaste etta − starkaste nolla [V]")
    ax3.set_title(f"Ögonöppning vid {bithastighet} bit/s")

    for ax in (ax1, ax2, ax3):
        if troskel is not None:
            ax.axvline(troskel, color="gray", linestyle=":", label=f"slår om från {troskel:g} V")
        ax.axvline(optimal, color="green", linestyle="--", label=f"optimal {optimal:g} V")
        ax.set_xlabel("Drivamplitud [V]")
        ax.grid(True)
        ax.legend(fontsize=8)

    plt.tight_layout()
    plt.show()


def main():
    # Samma AO-takt som vid sändning, så att drivfrekvensen blir den länken använder.
    ao_rate = ao_sampel_per_bit(step_time) / step_time
    verklig_frekvens = ao_rate / 2
    bithastighet = round(1 / step_time)
    amplituder = [a for a in AMPLITUDER if a <= AO_MAX_SPANNING]

    print(f"Provar {len(amplituder)} amplituder från {min(amplituder):g} till "
          f"{max(amplituder):g} V,")
    print(f"vid drivfrekvens {verklig_frekvens:.0f} Hz och ögontest vid {bithastighet} bit/s "
          "(från parametrar.py).")
    if not input(f"Tål LC-kristallen ±{max(amplituder):g} V? Fortsätt [j/n] ").strip() \
            .lower().startswith("j"):
        print("Avbrutet. Sänk de högsta värdena i AMPLITUDER och kör igen.")
        return

    print("\n  Amplitud   Kontrast   Rippel   Effektiv   Stigtid   Falltid   Ögonöppning")
    print("       [V]        [V]      [V]        [V]      [ms]      [ms]           [V]")
    resultat = []
    for amplitud in amplituder:
        r = mat_amplitud(amplitud, ao_rate, bithastighet)
        resultat.append(r)
        print(f"  {amplitud:8.1f}   {r['kontrast']:8.3f}   {r['rippel']:6.3f}"
              f"   {r['effektiv']:8.3f}    {ms(r['stigtid'])}    {ms(r['falltid'])}"
              f"        {r['ogon']:6.3f}")

    if max(r["kontrast"] for r in resultat) <= 0:
        print("\nIngen amplitud gav någon kontrast. Kontrollera kopplingen och ljuskällan.")
        return

    troskel, optimal = valj(resultat)
    if troskel is not None:
        print(f"\nKristallen börjar slå om vid ungefär {troskel:g} V.")
    print(f"Optimal drivamplitud: {optimal:g} V (lägsta med minst {OPTIMAL_ANDEL:.0%} av bästa "
          "effektiva kontrast och ögonöppning)")
    if optimal == amplituder[-1]:
        print("Det var den högsta som provades – kristallen kanske vill ha mer, om den tål det.")

    if input("\nSpara drivamplituden till parametrar.py? [j/n] ").strip().lower().startswith("j"):
        tid = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        uppdatera_parametrar(
            drivamplitud=f"drivamplitud = {optimal!r}  # V; toppvärdet på växelspänningen "
                         f"(+/- drivamplitud); mätt {tid}"
                         + (f", kristallen slår om från ca {troskel:g} V" if troskel else ""),
        )
        print("Kör kalibrering.py igen – tröskel och bästa bithastighet kan ha ändrats.")
    plotta(resultat, troskel, optimal, bithastighet)


if __name__ == "__main__":
    main()
