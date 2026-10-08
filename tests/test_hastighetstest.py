import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import hastighetstest as h


class SpeedTests(unittest.TestCase):
    def args(self, *extra):
        return h.parse_args(['--simulate', '--amplitudes', '3', '--bit-ms', '4',
                             '--repeats', '1', '--confirm-bits', '0', '--no-plot',
                             '--no-save-raw', *extra])

    def ideal(self, bits, amplitude=3):
        return h.Capture(np.r_[np.full(80, .4), np.repeat(.4+3*np.asarray(bits), 80),
                               np.full(80, .4)], 20000, .004, 1., 80)

    def test_patterns_and_wire_polarity(self):
        for pattern in h.PATTERNS:
            bits = h.payload(pattern, 256, np.random.default_rng(6))
            frame = h.START+bits+h.SLUT
            self.assertTrue(all((bits+h.SLUT)[i:i+len(h.SLUT)] != h.SLUT for i in range(len(bits))))
            self.assertEqual(h.decode_signal(self.ideal(frame).signal, 80, 2., len(bits)), bits)
        np.testing.assert_array_equal(h.voltages([1, 0, 1, 1], 3), [3, 0, 3, -3])
        np.testing.assert_array_equal(h.voltages([1, 1, 1, 1], 5), [5, -5, 5, -5])

    def test_payload_errors_and_missing_frames(self):
        bits = h.payload('langa', 256, np.random.default_rng(0))
        frame = h.START+bits+h.SLUT
        capture = self.ideal(frame)
        start = 80+(len(h.START)+25)*80
        capture.signal[start:start+80] = .4 if bits[25] else 3.4
        row, _ = h.score_trial(capture, bits, 3, 2, 'screening', 'langa', 1)
        self.assertEqual(row['bitfel'], 1)
        self.assertEqual(row['paketfel'], 1)
        missing = self.ideal([0]*len(frame))
        row, _ = h.score_trial(missing, bits, 3, 2, 'screening', 'langa', 1)
        self.assertEqual(row['paketfel'], 1)
        self.assertIsNone(row['ber'])
        self.assertEqual(row['jamforda_bitar'], 0)

    def test_premature_stop_is_not_zero_ber(self):
        bits = [0]*64
        capture = self.ideal(h.START+[0]*16+h.SLUT+[0]*48+h.SLUT)
        row, _ = h.score_trial(capture, bits, 3, 2, 'screening', 'slump', 1)
        self.assertEqual(row['status'], 'fel_ramlangd')
        self.assertIsNone(row['bitfel'])
        self.assertIsNone(row['ber'])

    def test_calibration_validation_is_held_out(self):
        args = self.args()
        bits, split = h.training_pattern(np.random.default_rng(1))
        capture = h.simulate(bits, .004, 3, args, np.random.default_rng(2))
        result = h.calibrate(capture, bits, split, args)
        self.assertTrue(result['accepted'], result['reasons'])
        before = result['threshold_v']
        n, start = result['samples_per_bit'], result['start_sample']
        j = split+20
        capture.signal[start+j*n:start+(j+1)*n] = .4 if bits[j] else 3.4
        bad = h.calibrate(capture, bits, split, args)
        self.assertEqual(before, bad['threshold_v'])
        self.assertFalse(bad['accepted'])
        self.assertGreater(bad['validation_errors'], 0)

    def test_reject_flat_and_one_bad_polarity(self):
        args = self.args()
        bits, split = h.training_pattern(np.random.default_rng(1))
        for mode in ['flat', 'polarity']:
            capture = h.simulate(bits, .004, 3, args, np.random.default_rng(2))
            if mode == 'flat':
                capture.signal[:] = .4
            else:
                start = capture.pre+34
                for i, bit in enumerate(bits):
                    if bit and i % 2:
                        capture.signal[start+i*80:start+(i+1)*80] = .4
            self.assertFalse(h.calibrate(capture, bits, split, args)['accepted'])

    def test_partial_and_empty_groups_never_recommended(self):
        cal = {('screening', 3, 4): {'accepted': True, 'threshold_v': 2, 'separation_v': 3}}
        row, _ = h.score_trial(self.ideal(h.START+[0]*64+h.SLUT), [0]*64, 3, 2, 'screening', 'langa', 1)
        for rows in ([], [row]):
            summary = h.summarize(rows, cal, 'screening', 3, 4, ['langa', 'slump'], 2)
            self.assertFalse(summary['utan_observerade_fel'])
            self.assertEqual(h.select_candidates([summary]), [])

    def test_reference_required_for_response_times(self):
        args = self.args('--ao-monitor-ai', 'Dev1/ai1')
        bits, _ = h.training_pattern(np.random.default_rng(1))
        capture = h.simulate(bits, .004, 3, args, np.random.default_rng(2))
        result = h.transition_metrics(capture, 3, bits)
        self.assertTrue(result['available'])
        self.assertGreater(result['rise_10_90_ms'], 0)
        capture.monitor = None
        self.assertFalse(h.transition_metrics(capture, 3, bits)['available'])

    def test_run_outputs_and_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            args = self.args('--amplitudes', '3', '4', '--confirm-bits', '512',
                             '--save-raw', '--output', tmp)
            self.assertEqual(h.run(args), 0)
            folder = next(Path(tmp).iterdir())
            report = json.loads((folder/'rapport.json').read_text())
            confirmed = [s for s in report['summaries'] if s['fas'] == 'bekraftelse']
            self.assertEqual(len(confirmed), 2)
            self.assertTrue(all(s['jamforda_bitar'] >= 512 for s in confirmed))
            self.assertEqual(report['recommendation_stage'], 'bekraftelse')
            self.assertTrue(list((folder/'rasignaler').glob('*.npz')))

    def test_interruption_keeps_report_without_recommendation(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            args = self.args('--output', tmp)
            original, count = h.simulate, 0
            def interrupted(*a):
                nonlocal count
                count += 1
                if count == 3:
                    raise KeyboardInterrupt
                return original(*a)
            with patch.object(h, 'simulate', side_effect=interrupted):
                self.assertEqual(h.run(args), 130)
            report = json.loads((next(Path(tmp).iterdir())/'rapport.json').read_text())
            self.assertEqual(report['completed_packets'], 1)
            self.assertIsNone(report['best_measured_candidate'])
            self.assertFalse(report['summaries'][0]['komplett'])

    def test_ao_reset_on_capture_error(self):
        import types
        fake = types.SimpleNamespace(Task=lambda: (_ for _ in ()).throw(RuntimeError('hardware error')))
        constants = types.SimpleNamespace(AcquisitionType=object(), TerminalConfiguration=object())
        with patch.dict('sys.modules', {'nidaqmx': fake, 'nidaqmx.constants': constants}), patch.object(h, 'reset_output') as reset:
            with self.assertRaises(RuntimeError):
                h.acquire([1, 0], .004, 3, self.args())
            reset.assert_called_once_with('Dev1/ao0')

    def test_bad_arguments_fail_before_hardware(self):
        with contextlib.redirect_stderr(io.StringIO()):
            for extra in [('--amplitudes', 'nan'), ('--amplitudes', '0'), ('--bit-ms', '0.2'),
                          ('--amplitudes', '3', '3'), ('--sample-rate', '100000', '--ao-monitor-ai', 'Dev1/ai1')]:
                with self.subTest(extra=extra), self.assertRaises(SystemExit):
                    self.args(*extra)


if __name__ == '__main__':
    unittest.main()
