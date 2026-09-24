"""Automatiskt test av befintlig optisk länk. Se HASTIGHETSTEST.md."""
import argparse
import csv
import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np

START = [1, 1, 1, 1, 0, 0]
SLUT = [1, 0, 1, 1, 1, 0, 0, 1, 1, 0, 1, 1, 1]


def decode_signal(signal, n, threshold, payload_size):
    """Synka enbart mot startsekvensen, aldrig mot nyttodatans facit.

    Samma princip som taemot_synk: första godkända stigande flank,
    median i bitens mittersta tredjedel, fast bitklocka och slutord.
    """
    def bit(start, j):
        a = start + j * n + n // 3
        b = start + j * n + 2 * n // 3
        return int(np.median(signal[a:b]) >= threshold)

    edges = np.flatnonzero((signal[:-1] < threshold) & (signal[1:] >= threshold)) + 1
    for start in edges:
        if start + len(START) * n > len(signal):
            break
        if start >= n and np.median(signal[start-n:start]) >= threshold:
            continue
        if [bit(start, j) for j in range(len(START))] != START:
            continue
        result = []
        for j in range(len(START), len(START) + payload_size + len(SLUT) + 1):
            if start + (j + 1) * n > len(signal):
                return None
            result.append(bit(start, j))
            if len(result) >= len(SLUT) and result[-len(SLUT):] == SLUT:
                return result[:-len(SLUT)]
        return None
    return None


def payload(pattern, size, rng):
    # Slutord i nyttodata eller över nyttodata/slutord-gränsen skulle ge
    # förtida avslut även på en perfekt länk. Välj därför giltiga testramar.
    for _ in range(10000):
        if pattern == 'slump':
            bits = rng.integers(0, 2, size).tolist()
        elif pattern == 'vaxlande':
            bits = (np.arange(size) % 2).tolist()
        else:
            bits = ((np.arange(size) // 16) % 2).tolist()
        joined = bits + SLUT
        if all(joined[i:i+len(SLUT)] != SLUT for i in range(size)):
            return bits
        if pattern != 'slump':
            break
    raise ValueError('Kan inte skapa ram utan förtida slutord; ändra antal bitar.')


def acquire(frame, dt, args):
    import nidaqmx
    from nidaqmx.constants import AcquisitionType, TerminalConfiguration
    # Två hårdvarutajmade uppgifter. AI startar före AO och läses medan AO kör.
    rate = args.sample_rate
    pre = int(round(args.idle * rate))
    post = int(round(args.idle * rate))
    volts = np.asarray(frame, dtype=float) * 5 * (-1.0) ** np.arange(len(frame))
    volts = np.concatenate((volts, [0.0]))
    began = time.perf_counter()
    try:
        with nidaqmx.Task() as ai, nidaqmx.Task() as ao:
            ai.ai_channels.add_ai_voltage_chan(args.ai, terminal_config=TerminalConfiguration.RSE,
                                               min_val=-10, max_val=10)
            ai.timing.cfg_samp_clk_timing(rate, sample_mode=AcquisitionType.CONTINUOUS,
                                          samps_per_chan=max(int(rate * 2), pre + post))
            ao.ao_channels.add_ao_voltage_chan(args.ao, min_val=-5, max_val=5)
            ao.write(0.0)  # Känd vilonivå före AI-inspelningen.
            ao.timing.cfg_samp_clk_timing(1 / dt, sample_mode=AcquisitionType.FINITE,
                                          samps_per_chan=len(volts))
            actual_rate = ai.timing.samp_clk_rate
            actual_dt = 1 / ao.timing.samp_clk_rate
            ao.write(volts.tolist(), auto_start=False)
            ai.start()
            chunks = [ai.read(pre, timeout=args.idle + 5)]
            ao.start()
            remaining = int(np.ceil(len(volts) * actual_dt * actual_rate)) + post
            while remaining:
                count = min(remaining, max(1, int(actual_rate * .05)))
                chunks.append(ai.read(count, timeout=5))
                remaining -= count
            ao.wait_until_done(timeout=5)
        return np.concatenate(chunks), actual_rate, actual_dt, time.perf_counter() - began
    finally:
        # Återställ även efter fel eller Ctrl+C; misslyckande får inte döljas.
        with nidaqmx.Task() as reset:
            reset.ao_channels.add_ao_voltage_chan(args.ao, min_val=-5, max_val=5)
            reset.write(0.0)


def simulate(frame, dt, args, rng):
    n = round(args.sample_rate * dt)
    levels = np.repeat(frame, n).astype(float) * 4
    signal = np.concatenate((np.zeros(round(args.idle * args.sample_rate)), levels,
                             np.zeros(round(args.idle * args.sample_rate))))
    signal += rng.normal(0, .05, len(signal))
    return signal, args.sample_rate, dt, len(signal) / args.sample_rate


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--simulate', action='store_true')
    p.add_argument('--bit-ms', nargs='+', type=float, default=[8, 6, 4, 3, 2])
    p.add_argument('--repeats', type=int, default=10)
    p.add_argument('--bits', type=int, default=256)
    p.add_argument('--threshold', type=float, default=3.0)
    p.add_argument('--sample-rate', type=int, default=20000)
    p.add_argument('--idle', type=float, default=.2)
    p.add_argument('--seed', type=int, default=2026)
    p.add_argument('--ai', default='Dev1/ai0')
    p.add_argument('--ao', default='Dev1/ao0')
    p.add_argument('--save-raw', action='store_true')
    p.add_argument('--output', type=Path, default=Path('testresultat'))
    a = p.parse_args()
    if not (1 <= a.repeats <= 10000 and 1 <= a.bits <= 4096):
        p.error('repeats måste vara 1–10000 och bits 1–4096.')
    if not (1 <= a.sample_rate <= 100000 and np.isfinite(a.threshold)
            and np.isfinite(a.idle) and .01 <= a.idle <= 10):
        p.error('Ogiltig samplingsfrekvens, tröskel eller vilotid.')
    for ms in a.bit_ms:
        n = a.sample_rate * ms / 1000
        if not (np.isfinite(ms) and .2 <= ms <= 100 and n >= 12 and abs(n-round(n)) < 1e-6):
            p.error('Bittid måste vara 0,2–100 ms och ge minst 12 hela sampel/bit.')
    folder = a.output / (datetime.now().strftime('%Y%m%d_%H%M%S_%f') + ('_SIMULERING' if a.simulate else ''))
    folder.mkdir(parents=True, exist_ok=False)
    (folder / 'installningar.json').write_text(json.dumps(vars(a), default=str, indent=2), encoding='utf-8')
    rng = np.random.default_rng(a.seed)
    # Samma nyttodata vid samtliga hastigheter ger jämförbara försök.
    cases = [(pattern, i+1, payload(pattern, a.bits, rng))
             for pattern in ('vaxlande', 'langa', 'slump') for i in range(a.repeats)]
    rows = []
    columns = ['bittid_ms', 'monster', 'forsok', 'korrekt', 'paketfel', 'jamforda_bitar',
               'bitfel', 'ber', 'mottagna_bitar', 'sanda_bitar', 'tid_s', 'ramtid_s', 'status']
    print('SIMULERING – inga hårdvarumätningar' if a.simulate else 'Mäter AI0; kör inte annan DAQ-kod samtidigt.')
    print(f'Resultat: {folder.resolve()}', flush=True)
    with (folder / 'forsok.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for ms in a.bit_ms:
            for pattern, trial, expected in cases:
                frame = START + expected + SLUT
                # Hårdvarufel avbryter testet, de är inte optiska paketfel.
                signal, rate, dt, elapsed = (simulate(frame, ms/1000, a, rng) if a.simulate
                                             else acquire(frame, ms/1000, a))
                n = rate * dt
                if abs(n-round(n)) > 1e-6:
                    raise RuntimeError('DAQ avrundade klockan till icke-heltal sampel/bit. Välj annan bittid.')
                received = decode_signal(signal, round(n), a.threshold, len(expected))
                comparable = received is not None and len(received) == len(expected)
                errors = sum(x != y for x, y in zip(expected, received)) if comparable else ''
                ok = comparable and errors == 0
                row = dict(zip(columns, [dt*1000, pattern, trial, int(ok), int(not ok),
                    len(expected) if comparable else 0, errors, errors/len(expected) if comparable else '',
                    len(received) if received is not None else 0, len(expected), elapsed, len(frame)*dt,
                    'ok' if ok else ('bitfel' if comparable else 'synk_eller_ramfel')]))
                writer.writerow(row)
                f.flush()
                rows.append(row)
                if a.save_raw:
                    np.savez_compressed(folder / f'{ms:g}ms_{pattern}_{trial}.npz', signal=signal,
                                        expected=expected, sample_rate=rate, bit_time=dt)
                print(f'{ms:g} ms {pattern} {trial}/{a.repeats}: {row["status"]}', flush=True)
    summaries = []
    for ms in sorted({r['bittid_ms'] for r in rows}, reverse=True):
        group = [r for r in rows if r['bittid_ms'] == ms]
        compared = sum(r['jamforda_bitar'] for r in group)
        failed = sum(r['paketfel'] for r in group)
        summaries.append(dict(bittid_ms=ms, paket=len(group), paketfel=failed,
            paketfelsandel=failed/len(group), jamforda_bitar=compared,
            ber=sum(r['bitfel'] for r in group if r['bitfel'] != '')/compared if compared else '',
            korrekta_nyttobitar_per_s=sum(r['korrekt']*r['sanda_bitar'] for r in group)/sum(r['tid_s'] for r in group)))
    with (folder / 'sammanfattning.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    eligible = [s for s in summaries if s['paketfel'] == 0]
    if eligible:
        best = max(eligible, key=lambda s: s['korrekta_nyttobitar_per_s'])
        print(f'Bäst bland inställningar utan observerade paketfel: {best["bittid_ms"]:g} ms, '
              f'{best["korrekta_nyttobitar_per_s"]:.1f} nyttobitar/s. Bekräfta med längre test.')
    else:
        print('Alla inställningar hade paketfel. Ingen felfri kandidat hittades.')
    print(f'Klart: {folder.resolve()}')


if __name__ == '__main__':
    main()
