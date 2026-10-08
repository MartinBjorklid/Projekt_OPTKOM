"""Amplitud- och hastighetstest med projektets 0/±V-signal. Se HASTIGHETSTEST.md.

Fristående: numpy krävs, nidaqmx för hårdvara och matplotlib för diagram.
Simuleringen kontrollerar programmet; den är inte en modell av LC-cellen.
"""
import argparse
import contextlib
import csv
import io
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

START = [1, 1, 1, 1, 0, 0]
SLUT = [1, 0, 1, 1, 1, 0, 0, 1, 1, 0, 1, 1, 1]
PATTERNS = ('vaxlande_01', 'vaxlande_10', 'langa',
            'ensamma_ettor', 'ensamma_nollor', 'slump')


@dataclass
class Capture:
    signal: np.ndarray
    rate: float
    dt: float
    elapsed: float
    pre: int
    monitor: object = None
    simulated: bool = False


def voltages(bits, amplitude):
    """Samma bitpositionsstyrda polaritet som skicka.py."""
    return np.asarray(bits, float)*amplitude*(-1.0)**np.arange(len(bits))


def payload(pattern, size, rng):
    i = np.arange(size)
    patterns = {'vaxlande': i % 2, 'vaxlande_01': i % 2, 'vaxlande_10': 1-i % 2,
                'langa': (i//16) % 2, 'ensamma_ettor': (i % 17 == 8).astype(int),
                'ensamma_nollor': (i % 17 != 8).astype(int)}
    if pattern not in (*patterns, 'slump'):
        raise ValueError('Okänt bitmönster: '+pattern)
    for _ in range(10000 if pattern == 'slump' else 1):
        bits = rng.integers(0, 2, size).tolist() if pattern == 'slump' else patterns[pattern].tolist()
        joined = bits+SLUT
        # Även skarven till slutordet ska sakna förtida slutord.
        if all(joined[j:j+len(SLUT)] != SLUT for j in range(size)):
            return bits
    raise ValueError('Kan inte skapa giltig ram; ändra antal bitar.')


def bit_values(signal, count, n, start):
    return np.array([np.median(signal[start+i*n+n//3:start+i*n+2*n//3]) for i in range(count)])


def sample_count(capture, count):
    actual = capture.rate*capture.dt
    n = round(actual)
    if n < 25 or abs(actual-n)*count > n/12:
        raise ValueError('Verkliga klockor ger för få sampel/bit eller för stor bitklockdrift.')
    return n


def decode_recording(signal, n, threshold, payload_size):
    """Ordinarie startsynk och mittenmedian. Inget skickat facit används."""
    signal = np.asarray(signal)
    edges = np.flatnonzero((signal[:-1] < threshold) & (signal[1:] >= threshold))+1
    for start in edges:
        start = int(start)
        if start+len(START)*n > len(signal):
            break
        if start >= n and np.median(signal[start-n:start]) >= threshold:
            continue
        if (bit_values(signal, len(START), n, start) >= threshold).astype(int).tolist() != START:
            continue
        result = []
        for j in range(len(START), len(START)+payload_size+len(SLUT)+1):
            if start+(j+1)*n > len(signal):
                return {'bits': None, 'start': start, 'status': 'ofullstandig_ram'}
            result.append(int(np.median(signal[start+j*n+n//3:start+j*n+2*n//3]) >= threshold))
            if len(result) >= len(SLUT) and result[-len(SLUT):] == SLUT:
                return {'bits': result[:-len(SLUT)], 'start': start, 'status': 'ram'}
        return {'bits': None, 'start': start, 'status': 'slutsekvens_saknas'}
    return {'bits': None, 'start': None, 'status': 'startsekvens_saknas'}


def decode_signal(signal, n, threshold, payload_size):
    return decode_recording(signal, n, threshold, payload_size)['bits']


def reset_output(channel):
    import nidaqmx
    with nidaqmx.Task() as task:
        task.ao_channels.add_ao_voltage_chan(channel, min_val=-10, max_val=10)
        task.write(0.0)


def acquire(bits, dt, amplitude, args):
    import nidaqmx
    from nidaqmx.constants import AcquisitionType, TerminalConfiguration
    wave = np.r_[voltages(bits, amplitude), 0.0]
    began = time.perf_counter()
    try:
        with nidaqmx.Task() as ai, nidaqmx.Task() as ao:
            channels = [args.ai]+([args.ao_monitor_ai] if args.ao_monitor_ai else [])
            for channel in channels:
                ai.ai_channels.add_ai_voltage_chan(channel, terminal_config=TerminalConfiguration.RSE,
                                                   min_val=-10, max_val=10)
            ai.timing.cfg_samp_clk_timing(args.sample_rate, sample_mode=AcquisitionType.CONTINUOUS,
                                          samps_per_chan=max(1000, int(args.sample_rate*2)))
            ao.ao_channels.add_ao_voltage_chan(args.ao, min_val=-10, max_val=10)
            ao.write(0.0)
            ao.timing.cfg_samp_clk_timing(1/dt, sample_mode=AcquisitionType.FINITE, samps_per_chan=len(wave))
            rate, actual_dt = float(ai.timing.samp_clk_rate), 1/float(ao.timing.samp_clk_rate)
            ao.write(wave.tolist(), auto_start=False)
            ai.start()
            pre = max(1, round(args.idle*rate))
            chunks = [np.asarray(ai.read(pre, timeout=args.idle+5))]
            ao.start()
            remaining = math.ceil((len(wave)*actual_dt+args.idle+args.max_delay)*rate)
            while remaining:
                count = min(remaining, max(2, int(rate*.05)))
                chunks.append(np.asarray(ai.read(count, timeout=5)))
                remaining -= count
            ao.wait_until_done(timeout=5)
        data = np.concatenate(chunks, axis=-1)
        signal, monitor = (data, None) if len(channels) == 1 else (data[0], data[1])
        if not np.isfinite(data).all():
            raise ValueError('DAQ gav ogiltiga sampel.')
        return Capture(signal, rate, actual_dt, time.perf_counter()-began, pre, monitor)
    finally:
        # Även vid undantag/Ctrl+C. Ett återställningsfel avbryter körningen.
        reset_output(args.ao)


def simulate(bits, dt, amplitude, args, rng):
    """Påhittad amplitudberoende nivå/lågpassrespons, endast för programkontroll."""
    n, pre = round(args.sample_rate*dt), round(args.idle*args.sample_rate)
    delay, tail = round(.0017*args.sample_rate), math.ceil((args.idle+args.max_delay)*args.sample_rate)
    drive = np.r_[np.zeros(pre+delay), np.repeat(voltages(bits, amplitude), n), np.zeros(tail)]
    desired = .4+4.2*(1-np.exp(-np.abs(drive)/2.5))
    signal = np.empty_like(desired)
    signal[0] = desired[0]
    alpha = 1-np.exp(-1/(args.sample_rate*.00035))
    for i in range(1, len(signal)):
        signal[i] = signal[i-1]+alpha*(desired[i]-signal[i-1])
    if args.condition in ('blocked-laser', 'constant-light'):
        signal[:] = .4 if args.condition == 'blocked-laser' else 2.0
    elif args.condition == 'reduced-light':
        signal = .4+.5*(signal-.4)
    signal += rng.normal(0, .035, len(signal))
    return Capture(signal, args.sample_rate, dt, len(signal)/args.sample_rate, pre,
                   drive if args.ao_monitor_ai else None, True)


def training_pattern(rng):
    train = np.r_[np.zeros(32, int), np.ones(32, int), np.zeros(32, int), np.ones(32, int),
                  rng.integers(0, 2, 256)]
    return np.r_[train, rng.integers(0, 2, 256)].tolist(), len(train)


def level_statistics(values, bits, indices):
    labels = np.asarray(bits)
    groups = {'zero': values[labels == 0],
              'one_positive_ao': values[(labels == 1) & (indices % 2 == 0)],
              'one_negative_ao': values[(labels == 1) & (indices % 2 == 1)]}
    return {name: {'count': len(v), 'median_v': float(np.median(v)) if len(v) else None,
                   'q05_v': float(np.quantile(v, .05)) if len(v) else None,
                   'q95_v': float(np.quantile(v, .95)) if len(v) else None} for name, v in groups.items()}


def separation(stats):
    if not all(s['count'] for s in stats.values()):
        return None
    return min(stats['one_positive_ao']['q05_v'], stats['one_negative_ao']['q05_v'])-stats['zero']['q95_v']


def calibrate(capture, bits, split, args):
    """Passa tid/tröskel på träningen, lås dem och kontrollera separat validering."""
    n = sample_count(capture, len(bits))
    signal, first = capture.signal, capture.pre
    last = min(first+round(args.max_delay*capture.rate), len(signal)-len(bits)*n)
    if last < first:
        raise ValueError('För kort kalibreringsinspelning.')
    labels, idx = np.asarray(bits[:split]), np.arange(split)
    left, right = idx*n+n//3, idx*n+2*n//3
    summed, best = np.r_[0., np.cumsum(signal)], None
    for a in range(first, last+1, 256):
        starts = np.arange(a, min(a+256, last+1))
        v = (summed[starts[:, None]+right]-summed[starts[:, None]+left])/(right-left)
        low, high = v[:, labels == 0].mean(axis=1), v[:, labels == 1].mean(axis=1)
        fitted = np.where(labels[None, :] == 0, low[:, None], high[:, None])
        scores = np.mean((v-fitted)**2, axis=1)/np.maximum((high-low)**2, 1e-12)
        j = int(np.argmin(scores))
        candidate = (float(scores[j]), int(starts[j]))
        if best is None or candidate < best:
            best = candidate
    score, start = best
    values = bit_values(signal, len(bits), n, start)
    stats = level_statistics(values[:split], labels, idx)
    gap, reasons = separation(stats), []
    if any(s['count'] < 30 for s in stats.values()):
        reasons.append('För få träningsbitar i någon nivå-/polaritetsgrupp.')
    if gap is None or gap < args.min_margin:
        reasons.append('Otillräcklig nivåseparation eller omvänd signalpolaritet.')
    if np.any(np.abs(signal) >= 9.9):
        reasons.append('AI nära ±10 V; möjlig klippning.')
    if start in (first, last):
        reasons.append('Tidspassning vid sökfönstrets kant; öka --max-delay.')
    candidate = None if gap is None or gap < args.min_margin else float(
        (stats['zero']['q95_v']+min(stats['one_positive_ao']['q05_v'], stats['one_negative_ao']['q05_v']))/2)
    threshold = args.threshold if args.threshold is not None else candidate
    errors = None if threshold is None else int(np.sum((values[split:] >= threshold) != np.asarray(bits[split:])))
    if errors:
        reasons.append('Bitfel i separat kalibreringsvalidering.')
    return {'accepted': not reasons and threshold is not None, 'reasons': reasons,
            'threshold_v': threshold, 'candidate_threshold_v': candidate,
            'mode': 'fixed' if args.threshold is not None else 'auto',
            'validation_errors': errors, 'validation_bits': len(bits)-split,
            'level_statistics': stats, 'separation_v': gap, 'start_sample': start,
            'fit_score': score, 'sample_rate_hz': capture.rate, 'bit_time_s': capture.dt,
            'samples_per_bit': n}


def transition_metrics(capture, amplitude, bits):
    """Svarstid relativt uppmätt AO, aldrig relativt en beräknad vågform."""
    if capture.monitor is None:
        return {'available': False, 'reason': 'Ingen uppmätt AO-referens; ange --ao-monitor-ai.'}
    n, ref = sample_count(capture, len(bits)), np.abs(capture.monitor)
    rising = np.flatnonzero((ref[:-1] < amplitude/2) & (ref[1:] >= amplitude/2))+1
    rising = rising[rising >= capture.pre]
    if not len(rising):
        return {'available': False, 'reason': 'Ingen AO-flank hittades.'}
    rise = int(rising[0])
    falling = np.flatnonzero((ref[:-1] >= amplitude/2) & (ref[1:] < amplitude/2))+1
    target = rise+32*n
    falls = falling[np.abs(falling-target) < n/2]
    if not len(falls) or rise+64*n > len(capture.signal):
        return {'available': False, 'reason': 'AO-referensen täcker inte kalibreringens platåer.'}
    fall = int(falls[np.argmin(abs(falls-target))])
    low = float(np.median(capture.signal[rise-16*n:rise-n]))
    high = float(np.median(capture.signal[rise+16*n:rise+31*n]))
    if high <= low:
        return {'available': False, 'reason': 'Ingen positiv nivåskillnad.'}
    result = {'available': True, 'low_v': low, 'high_v': high, 'timing_reference': 'measured_ao',
              'rise_delay_10_ms': None, 'rise_10_90_ms': None,
              'fall_delay_10_ms': None, 'fall_90_10_ms': None}
    for name, edge in [('rise', rise), ('fall', fall)]:
        normalized = (capture.signal[edge:edge+32*n]-low)/(high-low)
        progress = normalized if name == 'rise' else 1-normalized
        crossings = [np.flatnonzero(progress >= q) for q in (.1, .9)]
        if all(len(x) for x in crossings) and crossings[1][0] >= crossings[0][0]:
            result[name+'_delay_10_ms'] = float(crossings[0][0]/capture.rate*1000)
            result['rise_10_90_ms' if name == 'rise' else 'fall_90_10_ms'] = float(
                (crossings[1][0]-crossings[0][0])/capture.rate*1000)
    return result


def score_trial(capture, expected, amplitude, threshold, phase, pattern, trial):
    frame = START+expected+SLUT
    n = sample_count(capture, len(frame))
    decoded = decode_recording(capture.signal, n, threshold, len(expected))
    received = decoded['bits']
    comparable = received is not None and len(received) == len(expected)
    errors = sum(a != b for a, b in zip(expected, received)) if comparable else None
    ok = comparable and errors == 0
    status = ('ok' if ok else 'bitfel') if comparable else ('fel_ramlangd' if received is not None else decoded['status'])
    row = {'fas': phase, 'amplitud_V': amplitude, 'bittid_ms': capture.dt*1000,
           'bithastighet': 1/capture.dt, 'monster': pattern, 'forsok': trial,
           'threshold_V': threshold, 'korrekt': int(ok), 'paketfel': int(not ok),
           'jamforda_bitar': len(expected) if comparable else 0, 'bitfel': errors,
           'ber': errors/len(expected) if comparable else None,
           'mottagna_bitar': len(received) if received is not None else 0, 'sanda_bitar': len(expected),
           'tid_s': capture.elapsed, 'ramtid_s': len(frame)*capture.dt, 'status': status,
           'ai_range_limit': int(np.any(np.abs(capture.signal) >= 9.9)),
           'ao_mean_V': float(np.mean(voltages(frame, amplitude))), 'separation_V': None,
           'min_threshold_margin_V': None, 'zero_q95_V': None,
           'one_positive_q05_V': None, 'one_negative_q05_V': None}
    # Facit används först EFTER oberoende synk/avkodning, endast för diagnostik.
    if comparable:
        values = bit_values(capture.signal, len(expected), n, decoded['start']+len(START)*n)
        stats = level_statistics(values, expected, np.arange(len(expected))+len(START))
        row.update(separation_V=separation(stats), zero_q95_V=stats['zero']['q95_v'],
                   one_positive_q05_V=stats['one_positive_ao']['q05_v'],
                   one_negative_q05_V=stats['one_negative_ao']['q05_v'])
        # 0101 innehåller bara en ett-polaritet. Bedöm befintliga grupper och
        # redovisa den saknade som tom; låtsas inte att båda har mätts.
        one_bounds = [s['q05_v'] for key, s in stats.items() if key != 'zero' and s['count']]
        if stats['zero']['count'] and one_bounds:
            row['min_threshold_margin_V'] = min(threshold-row['zero_q95_V'], min(one_bounds)-threshold)
    return row, decoded


def summarize(rows, calibrations, phase, amplitude, ms, patterns, required_packets):
    group = [r for r in rows if r['fas'] == phase and r['amplitud_V'] == amplitude and abs(r['bittid_ms']-ms) < .01]
    cal = calibrations.get((phase, amplitude, ms))
    compared, failed = sum(r['jamforda_bitar'] for r in group), sum(r['paketfel'] for r in group)
    errors = sum(r['bitfel'] for r in group if r['bitfel'] is not None)
    total_time, frame_time = sum(r['tid_s'] for r in group), sum(r['ramtid_s'] for r in group)
    margins = [r['min_threshold_margin_V'] for r in group if r['min_threshold_margin_V'] is not None]
    covered = all(sum(r['monster'] == p for r in group) >= required_packets//len(patterns) for p in patterns)
    complete = bool(cal and cal['accepted'] and len(group) == required_packets and covered)
    good = sum(r['korrekt']*r['sanda_bitar'] for r in group)
    return {'fas': phase, 'amplitud_V': amplitude, 'begard_bittid_ms': ms,
            'faktisk_bittid_ms': float(np.mean([r['bittid_ms'] for r in group])) if group else None,
            'kalibrering_godkand': bool(cal and cal['accepted']), 'komplett': complete,
            'threshold_V': cal['threshold_v'] if cal else None,
            'kalibrering_separation_V': cal['separation_v'] if cal else None,
            'paket': len(group), 'forvantade_paket': required_packets, 'paketfel': failed,
            'paketfelsandel': failed/len(group) if group else None,
            'jamforda_bitar': compared, 'bitfel': errors if compared else None,
            'ber': errors/compared if compared else None,
            'korrekta_nyttobitar_per_s': good/total_time if total_time else None,
            'korrekta_nyttobitar_per_ram_s': good/frame_time if frame_time else None,
            'min_threshold_margin_V': min(margins) if margins else None,
            'ai_range_limit': sum(r['ai_range_limit'] for r in group),
            'utan_observerade_fel': complete and failed == 0 and compared > 0 and not any(r['ai_range_limit'] for r in group)}


def select_candidates(summaries):
    chosen = []
    for amplitude in sorted({s['amplitud_V'] for s in summaries}):
        good = [s for s in summaries if s['amplitud_V'] == amplitude and s['utan_observerade_fel']]
        if good:
            chosen.append(min(good, key=lambda s: s['faktisk_bittid_ms']))
    return chosen


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False, default=str), encoding='utf-8')


def write_csv(path, rows):
    if rows:
        with path.open('w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def text_validation(candidates, measure, args, folder, raw):
    """Projektets Huffman/Hamming-kedja, efter oberoende fysisk ramavkodning."""
    if args.no_text_tests:
        return {'status': 'disabled', 'results': []}
    try:
        import Signalbehandling as codec
    except ImportError as exc:
        print('Texttest hoppades över: Signalbehandling.py saknas eller kunde inte importeras:', exc)
        return {'status': 'unavailable', 'reason': str(exc), 'results': []}
    results = []
    for candidate in candidates:
        amp, ms, threshold = candidate['amplitud_V'], candidate['begard_bittid_ms'], candidate['threshold_V']
        for index, text in enumerate(args.texts):
            text = text.lower()
            result = {'amplitud_V': amp, 'bittid_ms': ms, 'text': text, 'status': None,
                      'korrekt_text': False, 'bitfel_fore_hamming': None, 'mottagen_text': None,
                      'kodade_bitar': None, 'komprimerade_bitar': None,
                      'korrekta_komprimerade_bitar_per_s': None}
            try:
                compressed = codec.huffman_encode(text, codec.codes)
                encoded, matrix, padding = codec.encode(compressed)
            except (ValueError, KeyError) as exc:
                result['status'] = 'kodningsfel: '+str(exc)
                results.append(result)
                continue
            bits = encoded.tolist()
            result.update(kodade_bitar=len(bits), komprimerade_bitar=len(compressed))
            joined = bits+SLUT
            if any(joined[i:i+len(SLUT)] == SLUT for i in range(len(bits))):
                result['status'] = 'protokollfel_fortida_slutord'
                results.append(result)
                continue
            capture = measure(START+bits+SLUT, ms, amp)
            decoded = decode_recording(capture.signal, sample_count(capture, len(START)+len(bits)+len(SLUT)), threshold, len(bits))
            if args.save_raw:
                save_capture(raw/f'text_{amp:g}V_{ms:g}ms_{index:02d}.npz', capture, START+bits+SLUT, amp, threshold, decoded)
            if decoded['bits'] is None or len(decoded['bits']) != len(bits):
                result['status'] = 'synk_eller_ramfel'
            else:
                result['bitfel_fore_hamming'] = sum(a != b for a, b in zip(bits, decoded['bits']))
                try:
                    with contextlib.redirect_stdout(io.StringIO()):
                        received = codec.huffman_decode(codec.decode(decoded['bits'], matrix, padding), codec.tree)
                    result.update(mottagen_text=received, korrekt_text=received == text,
                                  status='ok' if received == text else 'fel_text',
                                  korrekta_komprimerade_bitar_per_s=len(compressed)/capture.elapsed if received == text else 0.)
                except (ValueError, IndexError) as exc:
                    result['status'] = 'avkodningsfel: '+str(exc)
            results.append(result)
            write_csv(folder/'textforsok.csv', results)
            print(f'Texttest ±{amp:g} V {ms:g} ms: {text!r}: {result["status"]}', flush=True)
    write_csv(folder/'textforsok.csv', results)
    return {'status': 'complete', 'results': results}


def save_capture(path, capture, bits, amplitude, threshold=None, decoded=None, split=None):
    values = dict(signal=capture.signal, sent_bits=np.asarray(bits, int), sample_rate=capture.rate,
                  bit_time=capture.dt, amplitude_peak=amplitude, pre_samples=capture.pre,
                  simulated=capture.simulated)
    if capture.monitor is not None:
        values['ao_measured'] = capture.monitor
    if threshold is not None:
        values['threshold'] = threshold
    if split is not None:
        values['training_count'] = split
    if decoded is not None:
        values['decoded_bits'] = np.asarray(decoded['bits'] or [], int)
        values['receiver_start_sample'] = -1 if decoded['start'] is None else decoded['start']
    np.savez_compressed(path, **values)


def save_eye(capture, decoded, frame_count, path, threshold):
    import matplotlib.pyplot as plt
    n = sample_count(capture, frame_count)
    fig, ax = plt.subplots(figsize=(8, 4))
    for j in range(min(frame_count-1, 200)):
        segment = capture.signal[decoded['start']+j*n:decoded['start']+(j+2)*n]
        if len(segment) == 2*n:
            ax.plot(np.arange(2*n)/n, segment, color='tab:blue', alpha=.12, lw=.7)
    ax.axhline(threshold, color='tab:red', label=f'Tröskel {threshold:.3f} V')
    ax.axvspan(1/3, 2/3, color='tab:green', alpha=.12, label='Mottagarens avläsningsfönster')
    ax.set(xlabel='Tid / bittid', ylabel='TIA [V]',
           title='Ögondiagram, synk från mottagaren'+(' — SIMULERING' if capture.simulated else ''))
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def plot_summary(summaries, folder):
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.5))
    for amplitude in sorted({s['amplitud_V'] for s in summaries}):
        group = sorted([s for s in summaries if s['fas'] == 'screening' and s['amplitud_V'] == amplitude and s['paket']],
                       key=lambda s: s['faktisk_bittid_ms'], reverse=True)
        x = [1000/s['faktisk_bittid_ms'] for s in group]
        for ax, key in zip(axes, ['ber', 'paketfelsandel', 'korrekta_nyttobitar_per_s']):
            ax.plot(x, [s[key] if s[key] is not None else np.nan for s in group], 'o-', label=f'±{amplitude:g} V')
    for ax, title in zip(axes, ['Bitfelsandel i jämförbara ramar', 'Paketfelsandel', 'Korrekt nyttodata [bit/s]']):
        ax.set(xlabel='Nominell bithastighet [bit/s]', title=title)
        ax.grid(alpha=.3)
        ax.legend()
    for ax in axes[:2]:
        ax.set_ylim(bottom=0, top=max(.05, ax.get_ylim()[1]))
    if folder.name.endswith('_SIMULERING'):
        fig.suptitle('SIMULERING — verifierar programmet, inte LC-cellens prestanda')
    fig.tight_layout()
    fig.savefig(folder/'sammanfattning.png', dpi=140)
    plt.close(fig)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--amplitudes', nargs='+', type=float, default=[3, 4, 5])
    p.add_argument('--bit-ms', nargs='+', type=float, default=[32, 16, 10, 6, 4])
    p.add_argument('--patterns', nargs='+', choices=PATTERNS, default=list(PATTERNS))
    p.add_argument('--repeats', type=int, default=3, help='Screeningpaket per mönster och inställning.')
    p.add_argument('--bits', type=int, default=256)
    p.add_argument('--confirm-bits', type=int, default=10000, help='Minsta bitar per amplituds bästa kandidat; 0 hoppar över.')
    p.add_argument('--threshold', type=float, help='Fast tröskel i stället för automatisk; valideras ändå.')
    p.add_argument('--min-margin', type=float, default=.1, help='Minsta 5–95-percentilseparation vid kalibrering, V.')
    p.add_argument('--sample-rate', type=int, default=20000)
    p.add_argument('--idle', type=float, default=.2)
    p.add_argument('--max-delay', type=float, default=.25)
    p.add_argument('--seed', type=int, default=2026)
    p.add_argument('--ai', default='Dev1/ai0')
    p.add_argument('--ao', default='Dev1/ao0')
    p.add_argument('--ao-monitor-ai', help='Separat AI-kanal fysiskt ansluten till LC-drivspänningen.')
    p.add_argument('--distance-m', type=float)
    p.add_argument('--polarizer-deg', nargs=2, type=float, metavar=('FIRST', 'SECOND'))
    p.add_argument('--lc-angle-deg', type=float)
    p.add_argument('--condition', choices=['normal', 'blocked-laser', 'constant-light', 'reduced-light'], default='normal')
    p.add_argument('--notes', default='')
    p.add_argument('--texts', nargs='+', default=['hej', 'test', 'optisk kommunikation'])
    p.add_argument('--no-text-tests', action='store_true', help='Hoppa över projektets Huffman/Hamming-kedja.')
    p.add_argument('--output', type=Path, default=Path('testresultat'))
    p.add_argument('--save-raw', dest='save_raw', action='store_true', default=True)
    p.add_argument('--no-save-raw', dest='save_raw', action='store_false')
    p.add_argument('--no-plot', action='store_true')
    p.add_argument('--simulate', action='store_true')
    a = p.parse_args(argv)
    if not (1 <= a.repeats <= 10000 and 64 <= a.bits <= 4096 and 0 <= a.confirm_bits <= 10000000):
        p.error('Kräver 1–10000 repetitioner, 64–4096 bitar/paket och 0–10000000 bekräftelsebitar.')
    if a.seed < 0:
        p.error('Slumpfrö måste vara icke-negativt.')
    if any(not text.strip() for text in a.texts):
        p.error('Textmeddelanden får inte vara tomma.')
    if not all(np.isfinite(x) and 0 < x <= 10 for x in a.amplitudes):
        p.error('Amplituder måste vara större än 0 och högst 10 V peak.')
    if any(len(set(xs)) != len(xs) for xs in (a.amplitudes, a.bit_ms, a.patterns)):
        p.error('Amplituder, bittider och mönster får inte ha dubbletter.')
    if not 250 <= a.sample_rate <= 100000/(2 if a.ao_monitor_ai else 1):
        p.error('AI-takten måste vara 250–100000 sampel/s totalt över AI-kanalerna.')
    if a.ao_monitor_ai == a.ai:
        p.error('AO-referensen kräver en annan AI-kanal än fotodioden.')
    for ms in a.bit_ms:
        n = a.sample_rate*ms/1000
        if not (np.isfinite(ms) and .2 <= ms <= 100 and n >= 25 and abs(n-round(n)) < 1e-6):
            p.error('Bittid måste vara 0,2–100 ms och ge minst 25 hela sampel/bit.')
    if a.threshold is not None and not (np.isfinite(a.threshold) and -10 < a.threshold < 10):
        p.error('Fast tröskel ska ligga mellan −10 och 10 V.')
    if not (np.isfinite(a.min_margin) and 0 < a.min_margin <= 10):
        p.error('Minsta marginal ska vara större än 0 och högst 10 V.')
    if not (np.isfinite(a.idle) and .01 <= a.idle <= 5 and np.isfinite(a.max_delay) and .001 <= a.max_delay <= 5):
        p.error('Ogiltig vilotid eller fördröjningsmarginal.')
    if a.distance_m is not None and not (np.isfinite(a.distance_m) and a.distance_m > 0):
        p.error('Avstånd måste vara ändligt och positivt.')
    if any(not np.isfinite(x) for x in (a.polarizer_deg or [])+([] if a.lc_angle_deg is None else [a.lc_angle_deg])):
        p.error('Vinklar måste vara ändliga.')
    a.bit_ms.sort(reverse=True)
    return a


def run(args):
    began, now = time.perf_counter(), datetime.now().astimezone()
    folder = args.output/(now.strftime('%Y%m%d_%H%M%S_%f')+('_SIMULERING' if args.simulate else ''))
    folder.mkdir(parents=True, exist_ok=False)
    raw, eyes = folder/'rasignaler', folder/'ogondiagram'
    if args.save_raw:
        raw.mkdir()
    if not args.no_plot:
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot
            eyes.mkdir()
        except ImportError:
            print('matplotlib saknas; rådata och tabeller sparas utan diagram.')
            args.no_plot = True
    write_json(folder/'installningar.json', dict(vars(args), started_at=now.isoformat(), method_version=2,
        waveform='0/±A, polaritet enligt bitindex', bit_errors_before_fec=True,
        receiver_source='self-contained, taemot.py principles',
        simulation_model='synthetic, not an LC model' if args.simulate else None))
    estimated = len(args.amplitudes)*len(args.patterns)*args.repeats*sum(
        (args.bits+len(START)+len(SLUT))*ms/1000+2*args.idle+args.max_delay for ms in args.bit_ms)
    print('SIMULERING – inga hårdvarumätningar.' if args.simulate else 'Mäter projektets 0/±V-signal.')
    print(f'Screening: {len(args.amplitudes)*len(args.bit_ms)*len(args.patterns)*args.repeats} paket; '
          f'cirka {estimated/60:.0f} min signal/inspelning plus kalibrering, DAQ och filsparande.')
    print('Sedan bekräftelse av bästa godkända bittid per amplitud.' if args.confirm_bits else 'Bekräftelsetest avstängt.')
    print('Bitpositionsstyrd polaritet kan ge DC för vissa mönster, precis som nuvarande sändare.')
    print(f'Resultat: {folder.resolve()}', flush=True)
    rows, calibrations, summaries, eye_saved = [], {}, [], set()
    signal_rng = np.random.default_rng(np.random.SeedSequence([args.seed, 77]))
    status, failure, current = 'complete', None, None
    texts = {'status': 'not_started', 'results': []}

    def measure(bits, ms, amp):
        return simulate(bits, ms/1000, amp, args, signal_rng) if args.simulate else acquire(bits, ms/1000, amp, args)

    def prepare(phase, amp, ms):
        nonlocal current
        current = {'fas': phase, 'amplitud_V': amp, 'bittid_ms': ms, 'operation': 'kalibrering'}
        seed = np.random.SeedSequence([args.seed, 1 if phase == 'screening' else 2])
        bits, split = training_pattern(np.random.default_rng(seed))
        capture = measure(bits, ms, amp)
        if args.save_raw:
            save_capture(raw/f'{phase}_{amp:g}V_{ms:g}ms_kalibrering.npz', capture, bits, amp, split=split)
        result = calibrate(capture, bits, split, args)
        result.update(fas=phase, amplitude_peak_v=amp, requested_bit_ms=ms,
                      simulated=args.simulate,
                      transitions=transition_metrics(capture, amp, bits))
        calibrations[(phase, amp, ms)] = result
        write_json(folder/f'{phase}_{amp:g}V_{ms:g}ms_kalibrering.json', result)
        print(f'{phase} ±{amp:g} V, {ms:g} ms: '+
              (f'tröskel {result["threshold_v"]:.3f} V, separation {result["separation_v"]:.3f} V'
               if result['accepted'] else 'kalibrering underkänd: '+' '.join(result['reasons'])), flush=True)
        return result['accepted']

    columns = ['fas', 'amplitud_V', 'bittid_ms', 'bithastighet', 'monster', 'forsok', 'threshold_V',
        'korrekt', 'paketfel', 'jamforda_bitar', 'bitfel', 'ber', 'mottagna_bitar', 'sanda_bitar',
        'tid_s', 'ramtid_s', 'status', 'ai_range_limit', 'ao_mean_V', 'separation_V',
        'min_threshold_margin_V', 'zero_q95_V', 'one_positive_q05_V', 'one_negative_q05_V']

    def attempt(phase, amp, ms, pattern, trial, expected, writer, file):
        nonlocal current
        current = {'fas': phase, 'amplitud_V': amp, 'bittid_ms': ms,
                   'monster': pattern, 'forsok': trial, 'operation': 'paket'}
        frame, threshold = START+expected+SLUT, calibrations[(phase, amp, ms)]['threshold_v']
        capture = measure(frame, ms, amp)
        row, decoded = score_trial(capture, expected, amp, threshold, phase, pattern, trial)
        stem = f'{phase}_{amp:g}V_{ms:g}ms_{pattern}_{trial:04d}'
        if args.save_raw:
            save_capture(raw/(stem+'.npz'), capture, frame, amp, threshold, decoded)
        writer.writerow(row)
        file.flush()
        rows.append(row)
        key = (phase, amp, ms, pattern)
        if not args.no_plot and decoded['start'] is not None and key not in eye_saved:
            save_eye(capture, decoded, len(frame), eyes/(stem+'.png'), threshold)
            eye_saved.add(key)
        print(f'{phase} ±{amp:g} V {ms:g} ms {pattern} {trial}: {row["status"]}', flush=True)

    try:
        with (folder/'forsok.csv').open('w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=columns)
            writer.writeheader()
            for speed_index, ms in enumerate(args.bit_ms):
                for amp in np.roll(args.amplitudes, speed_index).tolist():
                    prepare('screening', amp, ms)
                rng = np.random.default_rng(np.random.SeedSequence([args.seed, 3]))
                for trial in range(1, args.repeats+1):
                    for pattern_index, pattern in enumerate(args.patterns):
                        expected = payload(pattern, args.bits, rng)
                        for amp in np.roll(args.amplitudes, trial+pattern_index+speed_index).tolist():
                            if calibrations[('screening', amp, ms)]['accepted']:
                                attempt('screening', amp, ms, pattern, trial, expected, writer, file)
                for amp in args.amplitudes:
                    summaries.append(summarize(rows, calibrations, 'screening', amp, ms,
                                               args.patterns, args.repeats*len(args.patterns)))
                write_csv(folder/'sammanfattning.csv', summaries)
            candidates = select_candidates(summaries)
            if args.confirm_bits:
                repeats = math.ceil(args.confirm_bits/(len(args.patterns)*args.bits))
                valid = []
                for candidate in candidates:
                    amp, ms = candidate['amplitud_V'], candidate['begard_bittid_ms']
                    if prepare('bekraftelse', amp, ms):
                        valid.append((amp, ms))
                rng = np.random.default_rng(np.random.SeedSequence([args.seed, 4]))
                for trial in range(1, repeats+1):
                    for pattern_index, pattern in enumerate(args.patterns):
                        expected = payload(pattern, args.bits, rng)
                        for j in np.roll(np.arange(len(valid)), trial+pattern_index):
                            amp, ms = valid[int(j)]
                            attempt('bekraftelse', amp, ms, pattern, trial, expected, writer, file)
                for candidate in candidates:
                    summaries.append(summarize(rows, calibrations, 'bekraftelse', candidate['amplitud_V'],
                        candidate['begard_bittid_ms'], args.patterns, repeats*len(args.patterns)))
            phase = 'bekraftelse' if args.confirm_bits else 'screening'
            eligible = [s for s in summaries if s['fas'] == phase and s['utan_observerade_fel']]
            current = {'operation': 'texttest'}
            texts = text_validation(eligible, measure, args, folder, raw)
    except KeyboardInterrupt:
        status, failure = 'interrupted', 'Avbrutet av användaren.'
    except Exception as exc:
        status, failure = 'failed', f'{type(exc).__name__}: {exc}'
        if exc.__context__ is not None:
            failure += f' (föregående fel: {exc.__context__})'
    finally:
        for phase, amp, ms in calibrations:
            count = args.repeats if phase == 'screening' else math.ceil(args.confirm_bits/(len(args.patterns)*args.bits))
            item = summarize(rows, calibrations, phase, amp, ms, args.patterns, count*len(args.patterns))
            summaries = [s for s in summaries if (s['fas'], s['amplitud_V'], s['begard_bittid_ms']) != (phase, amp, ms)]
            summaries.append(item)
        write_csv(folder/'sammanfattning.csv', summaries)
        pattern_summaries = []
        for phase_name, amp, ms in calibrations:
            count = args.repeats if phase_name == 'screening' else math.ceil(args.confirm_bits/(len(args.patterns)*args.bits))
            for pattern in args.patterns:
                item = summarize(rows, calibrations, phase_name, amp, ms, [pattern], count)
                item['monster'] = pattern
                pattern_summaries.append(item)
        write_csv(folder/'monster_sammanfattning.csv', pattern_summaries)
        phase = 'bekraftelse' if args.confirm_bits else 'screening'
        eligible = [s for s in summaries if s['fas'] == phase and s['utan_observerade_fel']]
        if texts['status'] == 'complete':
            eligible = [s for s in eligible if len([t for t in texts['results'] if t['amplitud_V'] == s['amplitud_V']]) == len(args.texts)
                        and all(t['korrekt_text'] for t in texts['results'] if t['amplitud_V'] == s['amplitud_V'])]
        best = max(eligible, key=lambda s: (s['korrekta_nyttobitar_per_s'], -s['amplitud_V'])) if eligible else None
        if status != 'complete' or args.condition != 'normal':
            best = None
        write_json(folder/'rapport.json', {'status': status, 'error': failure, 'last_operation': current,
            'simulated': args.simulate, 'distance_m': args.distance_m, 'session_elapsed_s': time.perf_counter()-began,
            'recommendation_stage': phase, 'best_measured_candidate': best,
            'text_validation': texts,
            'confirmation_enabled': bool(args.confirm_bits), 'completed_packets': len(rows), 'summaries': summaries,
            'limits': ['Noll observerade fel bevisar inte felfri kommunikation.',
                       'BER gäller endast rätt ramlängd; läs även paketfelsandel.',
                       'Separat testmottagare; texttest använder Signalbehandling.py när tillgänglig, inte hela program.py.',
                       'Kalibrering använder träningsfacit; datapaket synkar utan facit.',
                       'ADC-flaggan utesluter inte TIA-mättnad.',
                       'Signalformen kan ge DC för vissa bitmönster.',
                       'Avstånd/ljus ändras manuellt mellan körningar.']})
        if not args.no_plot and summaries:
            try:
                plot_summary(summaries, folder)
            except Exception as exc:
                print('Sammanfattningsdiagram kunde inte sparas:', exc, file=sys.stderr)
        if failure:
            print(f'Mätningen {status}: {failure}', file=sys.stderr)
        elif best:
            prefix = 'SIMULERAD kandidat' if args.simulate else 'Bäst uppmätta kandidat'
            print(f'{prefix}: ±{best["amplitud_V"]:g} V, {best["faktisk_bittid_ms"]:.4g} ms, '
                  f'{best["korrekta_nyttobitar_per_s"]:.1f} korrekt nyttobitar/s; '
                  f'{best["jamforda_bitar"]} jämförda bitar, inga observerade paketfel.')
        else:
            print('Ingen rekommenderad inställning. Se kalibreringar och paketfel i rapporten.')
        print(f'Sparat: {folder.resolve()}')
    return 130 if status == 'interrupted' else 1 if status == 'failed' else 0 if best else 2


def main(argv=None):
    return run(parse_args(argv))


if __name__ == '__main__':
    sys.exit(main())
