"""Digitala loopbacktester; ingen NI-hårdvara krävs."""
import contextlib
import io
import unittest
from unittest.mock import patch, MagicMock

import numpy as np
import Signalbehandling as signal
from taemot import SynkadBitavkodare


class MultilevelTests(unittest.TestCase):
    def receive(self, bits, levels, chunk=17):
        frame = signal.frame_symbols(bits, levels)
        thresholds = [(i + .5) * 4 / (levels-1) for i in range(levels-1)]
        samples = np.concatenate((np.zeros(61), np.repeat(frame, 30) * 4 / (levels-1), np.zeros(30)))
        samples += np.random.default_rng(41).normal(0, .015, len(samples))
        decoder = SynkadBitavkodare(30, thresholds, levels=levels)
        for start in range(0, len(samples), chunk):
            decoder.mata_in(samples[start:start+chunk])
        self.assertTrue(decoder.klar)
        self.assertEqual(decoder.payload, list(bits))
        return decoder.payload

    def test_all_levels_and_padding(self):
        for levels in (2, 4, 8):
            for size in range(40):
                with self.subTest(levels=levels, size=size):
                    self.receive(np.random.default_rng(size).integers(0, 2, size).tolist(), levels)

    def test_every_symbol_and_stop_pattern_in_payload(self):
        for levels in (4, 8):
            bits = signal.symbols_to_bits(range(levels), levels) + signal.STOP_BITS * 3
            self.receive(bits, levels)

    def test_huffman_hamming_loopback(self):
        for levels in (2, 4, 8):
            text = 'hej världen!'
            encoded, H, padding = signal.encode(signal.huffman_encode(text, signal.codes))
            received = self.receive(encoded.tolist(), levels)
            self.assertEqual(signal.huffman_decode(signal.decode(received, H, padding), signal.tree), text)

    def test_boundary_is_upper_level(self):
        for levels in (2, 4, 8):
            thresholds = list(range(1, levels))
            decoder = SynkadBitavkodare(30, thresholds, levels=levels)
            for i, threshold in enumerate(thresholds):
                decoder.data = [threshold] * 30
                self.assertEqual(decoder._bit(0, 0), i+1)
                decoder.data = [threshold-.001] * 30
                self.assertEqual(decoder._bit(0, 0), i)

    def test_validation(self):
        for levels, thresholds in [(4, None), (8, [1]), (4, [1, 1, 2]), (4, [3, 2, 1]),
                                   (2, [float('nan')]), (2, [float('inf')])]:
            with self.assertRaises(ValueError):
                signal.validate_thresholds(thresholds, levels)
        for dt in (0, -1, float('nan'), float('inf'), .0001):
            with self.assertRaises(ValueError):
                signal.validate_step_time(dt)
        with self.assertRaises(ValueError):
            signal.frame_symbols([0] * 4097, 4)
        with self.assertRaises(ValueError):
            signal.bits_to_symbols([2], 4)

    def test_corrupt_or_truncated_frames(self):
        for levels in (4, 8):
            for position in (6, -1):
                frame = signal.frame_symbols([1, 0, 1, 1], levels)
                frame[position] = 0 if frame[position] else levels-1
                decoder = SynkadBitavkodare(30, np.arange(levels-1)+.5, levels=levels)
                with self.assertRaises(ValueError):
                    decoder.mata_in(np.concatenate((np.zeros(30), np.repeat(frame, 30))))
            frame = signal.frame_symbols([1, 0, 1], levels)[:-1]
            decoder = SynkadBitavkodare(30, np.arange(levels-1)+.5, levels=levels)
            decoder.mata_in(np.concatenate((np.zeros(30), np.repeat(frame, 30))))
            self.assertFalse(decoder.klar)

    def test_cli_spellings(self):
        for spelling in (['--step-time'], ['--step_time'], ['--step', 'time']):
            args = signal.communication_args(['--levels', '4', '--threshold', '1', '2', '3'] + spelling + ['.01'])
            self.assertEqual(args.threshold, [1, 2, 3])
            self.assertEqual(args.step_time, .01)
            self.assertTrue(args.step_time_explicit)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            signal.communication_args(['--levels', '8', '--threshold', '1'])

    def test_sender_clock_amplitudes_and_reset(self):
        import skicka
        daq = MagicMock()
        task = daq.Task.return_value.__enter__.return_value
        with patch.dict('sys.modules', {'nidaqmx': daq}), patch.object(skicka, 'plt'):
            skicka.send_symbols(list(range(8)), step_time=.01, levels=8, extra_time=0)
        self.assertEqual(task.write.call_args_list[0].args[0],
                         signal.symbol_voltages(range(8), 8)+[0.0])
        self.assertEqual(task.timing.cfg_samp_clk_timing.call_args.kwargs['rate'], 100)
        self.assertEqual(task.write.call_args_list[-1].args[0], 0.0)

    def test_diagnostic_symbol_comparison(self):
        import felsok_kommunikation as debug
        for levels in (2, 4, 8):
            frame = np.asarray(signal.frame_symbols([1, 0, 1, 0], levels))
            with patch.object(debug, 'NIVAER', levels), patch.object(debug, 'TROSKEL', list(np.arange(levels-1)+.5)):
                raw = np.concatenate((np.zeros(60), np.repeat(frame, 30), np.zeros(60)))
                _, _, _, decoded = debug.hitta_basta_bitfas(raw, frame, 30)
                np.testing.assert_array_equal(decoded, frame)
                _, _, bits, _ = debug.fast_blockavkodning(raw, 30)
                self.assertEqual(bits, [1, 0, 1, 0])

    def test_program_passes_cli_to_both_ends(self):
        import threading
        import program
        for levels in (2, 4, 8):
            delivered = threading.Event()
            captured = {}
            thresholds = list(np.arange(levels-1)+.5)
            def receiver(**kwargs):
                captured['receive'] = kwargs
                kwargs['ready_event'].set()
                if not delivered.wait(2):
                    raise RuntimeError('Ingen sändning')
                return captured['bits']
            def sender(bits, **kwargs):
                captured['send'] = kwargs
                captured['bits'] = bits
                delivered.set()
            with patch.object(program, 'receive_continuous', side_effect=receiver), \
                 patch.object(program, 'send_payload', side_effect=sender), \
                 patch('builtins.input', return_value='hej'), contextlib.redirect_stdout(io.StringIO()) as out:
                program.main(['--levels', str(levels), '--threshold'] +
                             list(map(str, thresholds)) + ['--step-time', '.01'])
            self.assertIn('Stämmer med original: True', out.getvalue())
            for endpoint in ('send', 'receive'):
                self.assertEqual(captured[endpoint]['step_time'], .01)
                self.assertEqual(captured[endpoint]['levels'], levels)
            self.assertEqual(captured['receive']['threshold'], thresholds)

    def test_debug_cli_applies_to_both_modes(self):
        import felsok_kommunikation as debug
        for mode, function in [('1', 'enstaka_felsokning'), ('2', 'serietest')]:
            with patch('builtins.input', return_value=mode), patch.object(debug, function) as run:
                debug.main(['--levels', '4', '--threshold', '1', '2', '3', '--step', 'time', '.01'])
                run.assert_called_once()
                self.assertEqual(debug.BITTID, .01)
                self.assertEqual(debug.CLI_BITTID, .01)
                self.assertEqual(debug.TROSKEL, [1, 2, 3])
                self.assertEqual(debug.NIVAER, 4)
                self.assertGreaterEqual(debug.AI_FREKVENS * debug.BITTID, 25)


if __name__ == '__main__':
    unittest.main()
