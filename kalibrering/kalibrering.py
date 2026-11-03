"""Fristående AI0-kalibrering. Föreslår en tröskel men ändrar aldrig mottagaren.

Kör: python kalibrering.py --help
Kräver numpy; riktig mätning kräver även nidaqmx och NI-drivrutinen.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

import numpy as np


def training_pattern():
    rng = np.random.default_rng(20260925)
    # Långa platåer och blandade bitar; ettor på båda AO-polariteterna.
    training = np.r_[np.zeros(32, int), np.ones(32, int),
                     rng.integers(0, 2, 256)]
    validation = rng.integers(0, 2, 256)
    return np.r_[training, validation], len(training)


def capture(bits, *, ai_channel, ao_channel, step_time, sample_rate,
            idle, max_delay):
    """AI startar först; samlar även marginal för program- och optisk fördröjning."""
    import nidaqmx
    from nidaqmx.constants import AcquisitionType, TerminalConfiguration

    volts = np.r_[bits * 5.0 * (-1.0) ** np.arange(len(bits)), 0.0]
    try:
        with nidaqmx.Task() as ai, nidaqmx.Task() as ao:
            ai.ai_channels.add_ai_voltage_chan(
                ai_channel, terminal_config=TerminalConfiguration.RSE,
                min_val=-10.0, max_val=10.0)
            ai.timing.cfg_samp_clk_timing(
                sample_rate, sample_mode=AcquisitionType.CONTINUOUS,
                samps_per_chan=max(1000, int(sample_rate * 2)))
            ao.ao_channels.add_ao_voltage_chan(ao_channel, min_val=-5, max_val=5)
            ao.write(0.0)
            ao.timing.cfg_samp_clk_timing(
                1 / step_time, sample_mode=AcquisitionType.FINITE,
                samps_per_chan=len(volts))
            rate = float(ai.timing.samp_clk_rate)
            dt = 1 / float(ao.timing.samp_clk_rate)
            if rate * dt < 25:
                raise ValueError('Verklig samplingsfrekvens ger färre än 25 sampel/bit.')
            ao.write(volts.tolist(), auto_start=False)
            ai.start()
            pre = max(1, int(round(idle * rate)))
            chunks = [ai.read(pre, timeout=idle + 5)]
            ao.start()
            remaining = int(np.ceil((len(volts) * dt + max_delay + idle) * rate))
            while remaining:
                count = min(remaining, max(1, int(rate * .05)))
                chunks.append(ai.read(count, timeout=5))
                remaining -= count
            ao.wait_until_done(timeout=5)
        return np.concatenate(chunks), rate, dt, pre
    finally:
        # Återställ även efter avbrott. Dölj inte ett återställningsfel.
        with nidaqmx.Task() as reset:
            reset.ao_channels.add_ao_voltage_chan(ao_channel, min_val=-5, max_val=5)
            reset.write(0.0)


def bit_values(signal, bits_count, n, start):
    return np.array([
        np.median(signal[start + int(i * n + n / 3):
                         start + int(i * n + 2 * n / 3)])
        for i in range(bits_count)])


def analyse(signal, bits, training_count, *, sample_rate, step_time,
            earliest_start, max_delay, min_margin=.1, current_threshold=2.3):
    """Tidspassning på träningsdelen; separat valideringsdel ändrar inte tröskeln."""
    signal = np.asarray(signal, dtype=float)
    bits = np.asarray(bits, dtype=int)
    n = sample_rate * step_time
    if signal.ndim != 1 or not np.isfinite(signal).all() or n < 25:
        raise ValueError('Ogiltiga sampel eller färre än 25 sampel/bit.')
    last = min(earliest_start + int(max_delay * sample_rate),
               len(signal) - int(np.ceil(len(bits) * n)))
    if last <= earliest_start:
        raise ValueError('För kort inspelning för sökfönster och hela testmönstret.')

    # Sök start/fas utan befintlig tröskel. Anpassa två nivåer till känt facit.
    # Medelvärden används bara för tidsökningen; slutlig statistik använder median.
    indices = np.arange(training_count)
    left = (indices * n + n / 3).astype(int)
    right = (indices * n + 2 * n / 3).astype(int)
    summed = np.r_[0., np.cumsum(signal)]
    labels = bits[:training_count]
    zero, one = labels == 0, labels == 1
    best = None
    for first in range(earliest_start, last + 1, 256):
        starts = np.arange(first, min(first + 256, last + 1))
        values = (summed[starts[:, None] + right] -
                  summed[starts[:, None] + left]) / (right - left)
        v0 = values[:, zero].mean(axis=1)
        v1 = values[:, one].mean(axis=1)
        predicted = np.where(labels[None, :] == 0, v0[:, None], v1[:, None])
        score = np.mean((values - predicted) ** 2, axis=1) / np.maximum((v1-v0)**2, 1e-12)
        pos = int(np.argmin(score))
        candidate = (float(score[pos]), int(starts[pos]))
        if best is None or candidate < best:
            best = candidate
    fit_score, start = best
    values = bit_values(signal, len(bits), n, start)
    train = values[:training_count]
    groups = {'zero': train[zero],
              'one_positive_ao': train[one & (indices % 2 == 0)],
              'one_negative_ao': train[one & (indices % 2 == 1)]}
    stats = {name: {'count': len(v), 'median_v': float(np.median(v)),
                    'q05_v': float(np.quantile(v, .05)),
                    'q95_v': float(np.quantile(v, .95))}
             for name, v in groups.items()}
    low = stats['zero']['q95_v']
    high = min(stats['one_positive_ao']['q05_v'], stats['one_negative_ao']['q05_v'])
    gap = high - low
    reasons = []
    if any(s['count'] < 30 for s in stats.values()):
        reasons.append('För få träningsbitar i någon nivå-/polaritetsgrupp.')
    if gap < min_margin:
        reasons.append('Otillräcklig nivåseparation eller omvänd signalpolaritet.')
    if np.any(np.abs(signal) >= 9.9):
        reasons.append('AI nära mätområdets gräns ±10 V; möjlig klippning.')
    if start in (earliest_start, last):
        reasons.append('Bästa tidsläge ligger vid sökfönstrets kant; utöka mätningen.')
    threshold = float((low + high) / 2) if gap >= min_margin else None
    validation = values[training_count:]
    expected = bits[training_count:]
    errors = None if threshold is None else int(np.sum((validation >= threshold) != expected))
    if errors is not None and errors:
        reasons.append('Bitfel i den separata valideringsdelen.')
    return {
        'accepted': not reasons, 'reasons': reasons,
        'candidate_threshold_v': threshold,
        'recommended_threshold_v': threshold if not reasons else None,
        'current_threshold_v': current_threshold,
        'current_threshold_validation_errors': int(np.sum((validation >= current_threshold) != expected)),
        'candidate_validation_errors': errors, 'validation_bits': len(expected),
        'level_statistics': stats, 'separation_v': float(gap),
        'required_margin_v': min_margin, 'start_sample': start,
        'fit_score': fit_score, 'sample_rate_hz': sample_rate,
        'step_time_s': step_time, 'threshold_applied': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ai', default='Dev1/ai0')
    parser.add_argument('--ao', default='Dev1/ao0')
    parser.add_argument('--step-time', type=float, default=.004)
    parser.add_argument('--sample-rate', type=float, default=None,
                        help='Standard: samma översampling som mottagaren.')
    parser.add_argument('--idle', type=float, default=.2)
    parser.add_argument('--max-delay', type=float, default=.5)
    parser.add_argument('--min-margin', type=float, default=.1)
    parser.add_argument('--current-threshold', type=float, default=2.3)
    parser.add_argument('--output', type=Path, default=Path('kalibreringsresultat'))
    parser.add_argument('--simulate', action='store_true', help='Testdata, ingen DAQ används.')
    args = parser.parse_args()
    if not np.isfinite(args.step_time) or not .001 <= args.step_time <= .1:
        parser.error('--step-time måste vara 0,001–0,1 s.')
    rate = args.sample_rate if args.sample_rate is not None else max(25, round(5000 * args.step_time)) / args.step_time
    if not np.isfinite(rate) or not 250 <= rate <= 100000 or rate * args.step_time < 25:
        parser.error('Samplingsfrekvensen måste vara 250–100000 Hz och ge minst 25 sampel/bit.')
    for name in ('idle', 'max_delay', 'min_margin'):
        value = getattr(args, name)
        if not np.isfinite(value) or not .001 <= value <= 5:
            parser.error(f'{name} måste vara 0,001–5.')
    if not np.isfinite(args.current_threshold):
        parser.error('Nuvarande tröskel måste vara ändlig.')
    bits, split = training_pattern()
    if args.simulate:
        pre = round(args.idle * rate)
        start = pre + round(.037 * rate)
        length = start + int(np.ceil(len(bits) * args.step_time * rate)) + round((args.idle + args.max_delay) * rate)
        positions = np.floor((np.arange(length) - start) / (rate * args.step_time)).astype(int)
        active = (positions >= 0) & (positions < len(bits))
        signal = np.full(length, 1.0)
        signal[active] += 2.6 * bits[positions[active]]
        signal += np.random.default_rng(42).normal(0, .07, length)
        dt = args.step_time
    else:
        print('Mäter via AO/AI. Stäng andra program som använder samma DAQ.')
        signal, rate, dt, pre = capture(
            bits, ai_channel=args.ai, ao_channel=args.ao, step_time=args.step_time,
            sample_rate=rate, idle=args.idle, max_delay=args.max_delay)
    result = analyse(signal, bits, split, sample_rate=rate, step_time=dt,
                     earliest_start=pre, max_delay=args.max_delay,
                     min_margin=args.min_margin, current_threshold=args.current_threshold)
    now = datetime.now().astimezone()
    result.update(measured_at=now.isoformat(), date=now.date().isoformat(),
                  simulated=args.simulate, ai_channel=args.ai, ao_channel=args.ao,
                  method_version=1,
                  limitation='Känt testmönster; ordinarie startsynk och långa ramar är inte verifierade.')
    args.output.mkdir(parents=True, exist_ok=True)
    # Ny mapp per körning: tidigare mätningar skrivs aldrig över.
    import tempfile
    folder = Path(tempfile.mkdtemp(prefix=now.strftime('%Y%m%d-%H%M%S-'), dir=args.output))
    np.savez_compressed(folder / 'matdata.npz', signal=signal, bits=bits,
                        training_count=split, sample_rate=rate, step_time=dt)
    (folder / 'rapport.json').write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    print('SIMULERADE DATA' if args.simulate else 'UPPMÄTTA DATA')
    for name, s in result['level_statistics'].items():
        print(f"{name}: median {s['median_v']:.3f} V, 5–95 % {s['q05_v']:.3f}–{s['q95_v']:.3f} V")
    if result['accepted']:
        print(f"Föreslagen tröskel: {result['recommended_threshold_v']:.3f} V")
    else:
        print('Inget godkänt tröskelförslag: ' + ' '.join(result['reasons']))
    print(f"Valideringsfel med nuvarande tröskel: {result['current_threshold_validation_errors']}/{result['validation_bits']}")
    print(f"Valideringsfel med kandidat: {result['candidate_validation_errors']}/{result['validation_bits']}")
    print('Mottagarens tröskel är oförändrad. Rapport:', (folder / 'rapport.json').resolve())
    return 0 if result['accepted'] else 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as exc:
        print(f'Kalibreringen avbröts: {exc}', file=sys.stderr)
        sys.exit(1)
