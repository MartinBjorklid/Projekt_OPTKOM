import contextlib
import csv
import importlib.util
import io
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

spec = importlib.util.spec_from_file_location(
    "polarisator", Path(__file__).resolve().parents[1] / "polarisator_test.py")
polar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(polar)


class PolarisatorTests(unittest.TestCase):
    def test_sweep_and_waveform_extrema(self):
        self.assertEqual(polar.DEFAULT_AMPLITUDES, [2, 3, 4, 5, 6, 7])
        self.assertEqual(polar.DEFAULT_FREQUENCIES,
                         [20, 30, 40, 50, 60, 70, 80, 90, 100,
                          150, 200, 250, 300, 350, 400, 450, 500])
        for freq in polar.DEFAULT_FREQUENCIES:
            for form in ("sine", "square"):
                data, rate = polar.create_waveform(5000, freq, 7, form)
                self.assertAlmostEqual(data.mean(), 0, places=12)
                self.assertAlmostEqual(data.min(), -7)
                self.assertAlmostEqual(data.max(), 7)
                self.assertEqual(rate / len(data), freq)
                self.assertLessEqual(rate, 5000)

    def test_statistics_preserve_negative_tia_polarity(self):
        row = polar.summarize([-4, -2, -3], -1)
        self.assertEqual((row["min_V"], row["max_V"]), (-4, -2))
        self.assertEqual((row["min_minus_dark_V"], row["max_minus_dark_V"]), (-3, -1))
        self.assertEqual(row["peak_to_peak_V"], 2)
        with self.assertRaises(ValueError):
            polar.summarize([1, np.nan], 0)

    def test_bad_arguments_rejected_before_hardware(self):
        invalid = [
            ["--amplitudes", "nan", "--frequencies", "20"],
            ["--amplitudes", "11", "--frequencies", "20"],
            ["--amplitudes", "2", "--frequencies", "0"],
            ["--mode", "custom"],
            ["--mode", "sweep", "--sample-rate", "1000"],
            ["--mode", "sweep", "--duration", "nan"],
        ]
        for argv in invalid:
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    polar.parse_args(argv)
                self.assertEqual(error.exception.code, 2)

    def test_default_interactive_and_custom_selection(self):
        with patch("builtins.input", return_value=""):
            self.assertEqual(polar.parse_args([]).mode, "sweep")
        with patch("builtins.input", side_effect=["1", "3 4.5", "30 75"]):
            args = polar.parse_args([])
            self.assertEqual(args.amplitudes, [3, 4.5])
            self.assertEqual(args.frequencies, [30, 75])

    def test_complete_simulated_csv(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            polar.main(["--mode", "sweep", "--simulate", "--no-plot", "--output", folder])
            csv_file, = Path(folder).glob("*/matningar.csv")
            with csv_file.open() as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 102)
            self.assertEqual(len({(r["amplitude_peak_V"], r["requested_frequency_Hz"])
                                  for r in rows}), 102)
            self.assertTrue(all(r["simulation"] == "1" for r in rows))
            self.assertTrue(all(float(r["min_V"]) < float(r["max_V"]) for r in rows))

    def test_daq_order_and_cleanup_on_success_error_and_interrupt(self):
        args = polar.parse_args(["--amplitudes", "3", "--frequencies", "30"])
        for failure in (None, RuntimeError("read failed"), KeyboardInterrupt()):
            events = []
            class FakeTask:
                def __init__(self):
                    self.kind = len([e for e in events if e[0] == "create"])
                    events.append(("create", self.kind))
                    self.ao_channels = self.ai_channels = self
                    self.timing = self
                    self.out_stream = types.SimpleNamespace()
                    self.read_samples = 0

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    events.append(("close", self.kind))

                def add_ao_voltage_chan(self, channel, **kwargs):
                    events.append(("ao", channel))

                def add_ai_voltage_chan(self, channel, **kwargs):
                    events.append(("ai", channel))

                def cfg_samp_clk_timing(self, rate, **kwargs):
                    self.samp_clk_rate = rate

                def write(self, data, **kwargs):
                    events.append(("write", self.kind, data))

                def start(self):
                    events.append(("start", self.kind))

                def read(self, count, **kwargs):
                    if failure is not None:
                        raise failure
                    self.read_samples += count
                    if self.read_samples <= 5000:
                        return [-9.0] * count  # Insvingning får inte ingå i mätningen.
                    return np.linspace(-4, -2, count).tolist()

            constants = types.ModuleType("nidaqmx.constants")
            constants.AcquisitionType = types.SimpleNamespace(CONTINUOUS=1, FINITE=2)
            constants.RegenerationMode = types.SimpleNamespace(ALLOW_REGENERATION=1)
            constants.TerminalConfiguration = types.SimpleNamespace(RSE=1)
            module = types.ModuleType("nidaqmx")
            module.Task = FakeTask
            updates, settle_updates = [], []
            with patch.dict(sys.modules, {"nidaqmx": module, "nidaqmx.constants": constants}):
                if failure is None:
                    data, rate, freq = polar.acquire(
                        3, 30, args, on_data=lambda v, *rest: updates.append(v.copy()),
                        on_settle_data=lambda v, *rest: settle_updates.append(v.copy()))
                    self.assertEqual((data.min(), data.max(), rate, freq), (-4, -2, 5000, 30))
                    self.assertEqual(len(data), 10000)
                    self.assertEqual([len(v) for v in updates], list(range(500, 10001, 500)))
                    self.assertEqual(len(settle_updates[-1]), 5000)
                    self.assertTrue(np.all(settle_updates[-1] == -9))
                else:
                    with self.assertRaises(type(failure)):
                        polar.acquire(3, 30, args)
            self.assertLess(events.index(("start", 0)), events.index(("start", 1)))
            self.assertEqual([e for e in events if e[0] == "ai"], [("ai", "Dev1/ai0")])
            self.assertLess(events.index(("close", 0)), events.index(("write", 2, 0.0)))

    def test_interrupt_keeps_completed_csv_rows(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            with patch.object(polar, "acquire", side_effect=[
                    (np.array([-3.0, -1.0]), 5000, 20), KeyboardInterrupt()]):
                polar.main(["--amplitudes", "3", "--frequencies", "20", "30",
                            "--no-plot", "--output", folder])
            csv_file, = Path(folder).glob("*/matningar.csv")
            with csv_file.open() as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["min_V"], "-3.0")

    def test_all_completed_signals_are_plotted_and_saved(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()), \
                patch.object(plt, "show"), patch.object(plt, "pause"):
            polar.main(["--amplitudes", "2", "4", "--frequencies", "20",
                        "--simulate", "--duration", "0.1", "--output", folder])
            result, = Path(folder).iterdir()
            self.assertEqual(len(list((result / "tidskurvor").glob("*.png"))), 2)
            self.assertTrue((result / "min_max.png").is_file())
            self.assertTrue((result / "alla_matvarden.png").is_file())
            self.assertEqual(plt.get_fignums(), [])


if __name__ == "__main__":
    unittest.main()
