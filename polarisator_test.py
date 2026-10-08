"""Mät fotodiodens min/max vid egna LC-värden eller ett svep. Se POLARISATORTEST.md."""

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

DEFAULT_AMPLITUDES = list(range(2, 8))
DEFAULT_FREQUENCIES = list(range(20, 101, 10)) + list(range(150, 501, 50))
MAX_AO_RATE = 5000.0  # USB-6003: högst 5 kS/s på AO.


def create_waveform(rate, frequency, amplitude, waveform_type):
    """En hel, symmetrisk period; anpassa AO-klockan till vald frekvens."""
    if not (np.isfinite(rate) and 100 <= rate <= MAX_AO_RATE
            and np.isfinite(frequency) and 1 <= frequency <= rate / 4
            and np.isfinite(amplitude) and 0 <= amplitude <= 10
            and waveform_type in ("sine", "square")):
        raise ValueError("Ogiltig vågform, amplitud, frekvens eller samplingsfrekvens.")
    samples = int(np.floor(rate / frequency))
    samples -= samples % 4  # Sinus ska också nå exakt +amplitud och −amplitud.
    if waveform_type == "square":
        data = np.concatenate((np.full(samples // 2, amplitude),
                               np.full(samples // 2, -amplitude)))
    else:
        data = amplitude * np.sin(2 * np.pi * np.arange(samples) / samples)
    # AI behåller den valda samplingsfrekvensen. AO får en egen klocka
    # så t.ex. 300 Hz inte avrundas till en annan periodfrekvens.
    return data, frequency * samples


def set_output_zero(channel):
    import nidaqmx

    with nidaqmx.Task() as task:
        task.ao_channels.add_ao_voltage_chan(channel, min_val=-10, max_val=10)
        task.write(0.0)


def acquire(amplitude, frequency, args, on_data=None, on_settle_data=None):
    import nidaqmx
    from nidaqmx.constants import AcquisitionType, RegenerationMode, TerminalConfiguration

    wave, ao_rate = create_waveform(args.sample_rate, frequency, amplitude, args.waveform)
    try:
        with nidaqmx.Task() as ao, nidaqmx.Task() as ai:
            ao.ao_channels.add_ao_voltage_chan(args.ao, min_val=-10, max_val=10)
            ao.write(0.0)
            ao.timing.cfg_samp_clk_timing(
                ao_rate, sample_mode=AcquisitionType.CONTINUOUS, samps_per_chan=len(wave))
            ao.out_stream.regen_mode = RegenerationMode.ALLOW_REGENERATION
            ao.write(wave.tolist(), auto_start=False)
            actual_frequency = ao.timing.samp_clk_rate / len(wave)

            ai.ai_channels.add_ai_voltage_chan(
                args.ai, terminal_config=TerminalConfiguration.RSE, min_val=-10, max_val=10)
            ai.timing.cfg_samp_clk_timing(
                args.sample_rate, sample_mode=AcquisitionType.FINITE,
                samps_per_chan=max(2, int(np.ceil(args.sample_rate * args.duration))))
            actual_ai_rate = ai.timing.samp_clk_rate
            count = max(2, int(np.ceil(actual_ai_rate * args.duration)))
            settle_count = int(np.ceil(actual_ai_rate * args.settle))
            ai.timing.samp_quant_samp_per_chan = settle_count + count

            ao.start()
            ai.start()
            # Visa även insvängningen live, men håll dessa sampel utanför
            # mätfönstret och de sparade min/max-värdena.
            settle_chunks = []
            remaining = settle_count
            while remaining:
                block = min(remaining, max(2, int(actual_ai_rate * 0.1)))
                settling = np.asarray(ai.read(block, timeout=5))
                remaining -= block
                if on_settle_data is not None:
                    settle_chunks.append(settling)
                    on_settle_data(np.concatenate(settle_chunks), actual_ai_rate, actual_frequency)
            # Läs i korta block så tidsplotten kan uppdateras under mätningen.
            chunks = []
            remaining = count
            while remaining:
                block = min(remaining, max(2, int(actual_ai_rate * 0.1)))
                chunks.append(np.asarray(ai.read(block, timeout=5)))
                remaining -= block
                if on_data is not None:
                    on_data(np.concatenate(chunks), actual_ai_rate, actual_frequency)
            signal = np.concatenate(chunks)
        return signal, actual_ai_rate, actual_frequency
    finally:
        # Kontextblocken frigör uppgifterna innan AO nollställs, även vid Ctrl+C.
        # Ett återställningsfel får synas för användaren.
        set_output_zero(args.ao)


def simulate(amplitude, frequency, args, rng):
    """Syntetisk signal för programkontroll; beskriver inte LC-cellens respons."""
    count = max(2, int(np.ceil(args.sample_rate * args.duration)))
    t = np.arange(count) / args.sample_rate
    response = np.cos(2 * np.pi * frequency * t)
    if args.waveform == "square":
        response = np.where(response >= 0, 1.0, -1.0)
    signal = 1 + amplitude / 10 * response
    signal += rng.normal(0, 0.005, count)
    return signal, args.sample_rate, frequency


def summarize(signal, dark_level):
    signal = np.asarray(signal, dtype=float)
    if signal.ndim != 1 or signal.size < 2 or not np.all(np.isfinite(signal)):
        raise ValueError("Mätningen måste innehålla minst två giltiga AI-sampel.")
    low, high = float(signal.min()), float(signal.max())
    return {
        "min_V": low, "max_V": high, "peak_to_peak_V": high - low,
        "mean_V": float(signal.mean()), "std_V": float(signal.std(ddof=1)),
        "min_minus_dark_V": low - dark_level, "max_minus_dark_V": high - dark_level,
        "samples": signal.size,
        "ai_range_limit": int(low <= -10 or high >= 10),
    }


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=("custom", "sweep"))
    p.add_argument("--amplitudes", nargs="+", type=float, help="V peak, alltså ±V")
    p.add_argument("--frequencies", nargs="+", type=float, help="Hz")
    p.add_argument("--waveform", choices=("sine", "square"), default="sine")
    p.add_argument("--sample-rate", type=float, default=5000)
    p.add_argument("--settle", type=float, default=1.0, help="Insvingningstid i sekunder")
    p.add_argument("--duration", type=float, default=2.0, help="Mättid per kombination i sekunder")
    p.add_argument("--dark-level", type=float, default=0.0, help="Uppmätt mörkernivå på AI, V")
    p.add_argument("--ai", default="Dev1/ai0")
    p.add_argument("--ao", default="Dev1/ao0")
    p.add_argument("--output", type=Path, default=Path("polarisator_resultat"))
    p.add_argument("--save-raw", action="store_true")
    p.add_argument("--no-plot", action="store_true")
    p.add_argument("--simulate", action="store_true", help="Programtest utan DAQ")
    argv = sys.argv[1:] if argv is None else argv
    a = p.parse_args(argv)
    if not argv:
        try:
            choice = input("Välj 1 = egna värden, 2 = standardsvep [2]: ").strip()
            if choice not in ("", "1", "2"):
                p.error("Välj 1 eller 2.")
            a.mode = "custom" if choice == "1" else "sweep"
            if a.mode == "custom":
                a.amplitudes = [float(v) for v in input("Amplitud(er) i V peak, separera med blanksteg: ").split()]
                a.frequencies = [float(v) for v in input("Frekvens(er) i Hz, separera med blanksteg: ").split()]
        except (EOFError, ValueError):
            p.error("Ange tal separerade med blanksteg, eller kör med kommandoradsargument.")
    if a.mode is None:
        a.mode = "custom" if a.amplitudes is not None or a.frequencies is not None else "sweep"
    if a.mode == "custom":
        if not a.amplitudes or not a.frequencies:
            p.error("Egna värden kräver både --amplitudes och --frequencies.")
    elif a.amplitudes is not None or a.frequencies is not None:
        p.error("Använd --mode custom för egna amplituder/frekvenser.")
    else:
        a.amplitudes = DEFAULT_AMPLITUDES.copy()
        a.frequencies = DEFAULT_FREQUENCIES.copy()
    if not (np.isfinite(a.settle) and 0 <= a.settle <= 60
            and np.isfinite(a.duration) and 0.1 <= a.duration <= 60
            and np.isfinite(a.dark_level) and -10 <= a.dark_level <= 10):
        p.error("Insvingningstid ska vara 0–60 s, mättid 0,1–60 s och mörkernivå −10–10 V.")
    try:
        for amplitude in a.amplitudes:
            for frequency in a.frequencies:
                create_waveform(a.sample_rate, frequency, amplitude, a.waveform)
                if a.duration * frequency < 2:
                    p.error("Mättiden måste täcka minst två perioder vid varje frekvens.")
    except ValueError as exc:
        p.error(str(exc) + " Använd 0–10 V, 100–5000 sampel/s och minst fyra sampel/period.")
    return a


class SignalPlot:
    """Ett återanvänt fönster under svepet; spara varje komplett tidskurva."""

    def __init__(self):
        import matplotlib.pyplot as plt

        self.plt = plt
        plt.ion()
        self.fig, self.axes = plt.subplots(3, 1, figsize=(12, 10))
        self.lines = [ax.plot([], [], linewidth=1)[0] for ax in self.axes]
        for ax in self.axes:
            ax.set_xlabel("Tid [s]")
            ax.set_ylabel("Spänning [V]")
            ax.grid(True)
        self.axes[0].set_title("Fotodiod / TIA — hela mätningen")
        self.axes[1].set_title("Fotodiod / TIA — första fem LC-perioderna")
        self.axes[2].set_title("Beräknad AO-vågform — egen tidsaxel, inte uppmätt eller fassynkron med AI")
        self.limits = []
        for ax in self.axes[:2]:
            self.limits.append((ax.axhline(0, color="tab:green", linestyle="--", label="Min"),
                                ax.axhline(0, color="tab:red", linestyle="--", label="Max")))
            ax.legend(loc="upper right")
        self.fig.tight_layout(rect=(0, 0, 1, 0.95))

    def begin(self, amplitude, frequency, args, index, total):
        self.amplitude = amplitude
        self.frequency = frequency
        self.args = args
        self.progress = f"{index}/{total}"
        self.fig.suptitle(f"{self.progress}: ±{amplitude:g} V, {frequency:g} Hz — insvängning")
        for line in self.lines[:2]:
            line.set_data([], [])
        for low, high in self.limits:
            low.set_visible(False)
            high.set_visible(False)
        wave, rate = create_waveform(args.sample_rate, frequency, amplitude, args.waveform)
        reference = np.tile(wave, 3)
        self.lines[2].set_data(np.arange(len(reference)) / rate, reference)
        self.axes[2].relim()
        self.axes[2].autoscale_view()
        self.fig.canvas.draw_idle()
        self.plt.pause(0.001)

    def update_settle(self, signal, rate, frequency):
        self.update(signal, rate, frequency, settling=True)

    def update(self, signal, rate, frequency, settling=False):
        times = np.arange(len(signal)) / rate
        self.lines[0].set_data(times, signal)
        zoom = times < 5 / frequency
        self.lines[1].set_data(times[zoom], signal[zoom])
        low, high = float(np.min(signal)), float(np.max(signal))
        for ax, (low_line, high_line) in zip(self.axes[:2], self.limits):
            low_line.set_ydata([low, low])
            high_line.set_ydata([high, high])
            low_line.set_visible(True)
            high_line.set_visible(True)
            ax.relim()
            ax.autoscale_view()
        label = " — SIMULERING" if self.args.simulate else ""
        stage = "insvängning" if settling else "mätning"
        self.axes[0].set_title(f"Fotodiod / TIA — hela {stage}en")
        self.fig.suptitle(f"{self.progress}: ±{self.amplitude:g} V, {frequency:.3f} Hz — {stage}{label}\n"
                          f"Min {low:.5f} V | Max {high:.5f} V | Medel {np.mean(signal):.5f} V")
        self.fig.canvas.draw_idle()
        self.plt.pause(0.001)

    def save(self, folder, index):
        path = folder / "tidskurvor"
        path.mkdir(exist_ok=True)
        self.fig.savefig(path / f"{index:03d}_{self.amplitude:g}V_{self.frequency:g}Hz.png", dpi=120)

    def close(self):
        self.plt.close(self.fig)
        self.plt.ioff()


def plot_results(rows, folder):
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, 6))
    for amplitude in dict.fromkeys(r["amplitude_peak_V"] for r in rows):
        group = sorted((r for r in rows if r["amplitude_peak_V"] == amplitude),
                       key=lambda r: r["actual_frequency_Hz"])
        freq = [r["actual_frequency_Hz"] for r in group]
        line, = ax.plot(freq, [r["max_V"] for r in group], "o-", label=f"±{amplitude:g} V max")
        ax.plot(freq, [r["min_V"] for r in group], "x--", color=line.get_color(),
                label=f"±{amplitude:g} V min")
    ax.set_xlabel("Faktisk LC-frekvens [Hz]")
    ax.set_ylabel("Fotodiod / TIA, AI [V]")
    ax.set_title("Fotodiodens min/max" + (" — SIMULERING" if rows[0]["simulation"] else ""))
    ax.grid(True)
    ax.legend(ncol=2)
    fig.tight_layout()
    fig.savefig(folder / "min_max.png", dpi=150)
    summary, axes = plt.subplots(3, 2, figsize=(13, 12))
    metrics = [("min_V", "Min [V]"), ("max_V", "Max [V]"),
               ("mean_V", "Medelvärde [V]"), ("peak_to_peak_V", "Peak-to-peak [V]"),
               ("std_V", "Standardavvikelse [V]"),
               ("min_minus_dark_V", "Min/max minus mörkernivå [V]")]
    for ax, (key, title) in zip(axes.flat, metrics):
        for amplitude in dict.fromkeys(r["amplitude_peak_V"] for r in rows):
            group = sorted((r for r in rows if r["amplitude_peak_V"] == amplitude),
                           key=lambda r: r["actual_frequency_Hz"])
            freq = [r["actual_frequency_Hz"] for r in group]
            line, = ax.plot(freq, [r[key] for r in group], "o-", label=f"±{amplitude:g} V")
            if key == "min_minus_dark_V":
                line.set_label(f"±{amplitude:g} V min")
                ax.plot(freq, [r["max_minus_dark_V"] for r in group], "x--",
                        color=line.get_color(), label=f"±{amplitude:g} V max")
        ax.set_title(title)
        ax.set_xlabel("Faktisk LC-frekvens [Hz]")
        ax.set_ylabel("Spänning [V]")
        ax.grid(True)
        ax.legend(fontsize=8, ncol=2)
    summary.suptitle("Alla mätvärden" + (" — SIMULERING" if rows[0]["simulation"] else ""))
    summary.tight_layout(rect=(0, 0, 1, 0.96))
    summary.savefig(folder / "alla_matvarden.png", dpi=150)
    plt.show()
    plt.close(fig)
    plt.close(summary)


def main(argv=None):
    args = parse_args(argv)
    folder = args.output / (datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                            + ("_SIMULERING" if args.simulate else ""))
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "installningar.json").write_text(
        json.dumps(vars(args), default=str, ensure_ascii=False, indent=2), encoding="utf-8")
    cases = [(a, f) for a in args.amplitudes for f in args.frequencies]
    print("SIMULERING – syntetiska värden" if args.simulate else "Mäter fotodiod/TIA på " + args.ai)
    print(f"{len(cases)} kombinationer; amplituder i ±V. Resultat: {folder.resolve()}", flush=True)
    columns = ["amplitude_peak_V", "requested_frequency_Hz", "actual_frequency_Hz",
               "waveform", "ai_sample_rate_Hz", "duration_s", "settle_s", "dark_level_V",
               "min_V", "max_V", "peak_to_peak_V", "mean_V", "std_V",
               "min_minus_dark_V", "max_minus_dark_V", "samples", "ai_range_limit", "simulation"]
    rows = []
    rng = np.random.default_rng(2026)
    plot = None if args.no_plot else SignalPlot()
    with (folder / "matningar.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        file.flush()
        try:
            for i, (amplitude, frequency) in enumerate(cases, 1):
                if plot is not None:
                    plot.begin(amplitude, frequency, args, i, len(cases))
                signal, rate, actual_frequency = (
                    simulate(amplitude, frequency, args, rng) if args.simulate
                    else acquire(amplitude, frequency, args, on_data=plot.update if plot else None,
                                 on_settle_data=plot.update_settle if plot else None))
                row = dict(amplitude_peak_V=amplitude, requested_frequency_Hz=frequency,
                           actual_frequency_Hz=actual_frequency, waveform=args.waveform,
                           ai_sample_rate_Hz=rate, duration_s=len(signal) / rate,
                           settle_s=args.settle, dark_level_V=args.dark_level,
                           simulation=int(args.simulate), **summarize(signal, args.dark_level))
                writer.writerow(row)
                file.flush()  # Behåll färdiga mätpunkter om körningen avbryts.
                rows.append(row)
                if plot is not None:
                    plot.update(signal, rate, actual_frequency)
                    plot.save(folder, i)
                if args.save_raw:
                    np.savez_compressed(folder / f"{i:03d}_{amplitude:g}V_{frequency:g}Hz.npz",
                                        signal=signal, sample_rate=rate, frequency=actual_frequency,
                                        amplitude_peak=amplitude, simulation=args.simulate)
                print(f"{i}/{len(cases)} ±{amplitude:g} V, {actual_frequency:.3f} Hz: "
                      f"min={row['min_V']:.5f} V, max={row['max_V']:.5f} V, "
                      f"medel={row['mean_V']:.5f} V", flush=True)
                if row["ai_range_limit"]:
                    print("AI når mätområdets gräns (±10 V); min/max kan vara klippta.")
        except KeyboardInterrupt:
            print("\nAvbrutet. Färdiga mätpunkter finns sparade.")
        finally:
            if plot is not None:
                plot.close()
    print(f"Sparat: {folder / 'matningar.csv'}")
    if rows and not args.no_plot:
        plot_results(rows, folder)


if __name__ == "__main__":
    main()
