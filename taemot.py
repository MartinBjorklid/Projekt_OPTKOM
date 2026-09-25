import sys
from unittest.mock import MagicMock

sys.modules['nidaqmx'] = MagicMock()
sys.modules['nidaqmx.constants'] = MagicMock()

import time
import numpy as np
import nidaqmx
from nidaqmx.constants import AcquisitionType, TerminalConfiguration

from skicka import Start_seq, Slut_seq, step_time as STANDARD_BITTID

class SynkadBitavkodare:
    """Tar emot en ström av AI-sampel och avläser bitar i deras mitt."""

    def __init__(self, sampel_per_bit, threshold=2.3, max_payload_bits=4096):
        self.n = int(sampel_per_bit)
        if self.n < 12:
            raise ValueError("För få AI-sampel per bit; öka AI-samplingsfrekvensen.")
        self.threshold = float(threshold)
        self.max_payload_bits = int(max_payload_bits)
        self.data = []
        self.sok_index = 1
        self.start_index = None
        self.nasta_bit = len(Start_seq)
        self.payload = []
        self.klar = False

    def _bit(self, start_index, bitnummer):
        # Använd BARA mitten-tredjedelen av biten; undvik båda flankerna.
        a = start_index + bitnummer * self.n + self.n // 3
        b = start_index + bitnummer * self.n + 2 * self.n // 3
        return int(np.median(self.data[a:b]) >= self.threshold)

    def mata_in(self, sampel):
        if self.klar:
            return
        self.data.extend(sampel)
        startlangd = len(Start_seq) * self.n

        if self.start_index is None:
            while self.sok_index + startlangd <= len(self.data):
                i = self.sok_index
                self.sok_index += 1
                if not (self.data[i - 1] < self.threshold <= self.data[i]):
                    continue
                if i >= self.n and np.median(self.data[i-self.n:i]) >= self.threshold:
                    continue
                if [self._bit(i, j) for j in range(len(Start_seq))] == Start_seq:
                    self.start_index = i
                    print(f"Startsekvens bekräftad vid AI-sampel {i}. Synkroniserar.")
                    break

        if self.start_index is None:
            return

        while self.start_index + (self.nasta_bit + 1) * self.n <= len(self.data):
            bit = self._bit(self.start_index, self.nasta_bit)
            self.nasta_bit += 1
            self.payload.append(bit)

            if len(self.payload) >= len(Slut_seq) and self.payload[-len(Slut_seq):] == Slut_seq:
                self.payload = self.payload[:-len(Slut_seq)]
                self.klar = True
                return

            if len(self.payload) > self.max_payload_bits + len(Slut_seq):
                raise RuntimeError("Meddelandet överskred max_payload_bits.")


def receive_continuous(step_time=STANDARD_BITTID, channel="Dev1/ai0",
                       threshold=2.3, timeout_s=15.0, ready_event=None):
    if step_time <= 0:
        raise ValueError("step_time måste vara positiv.")
    
    sampel_per_bit = max(25, int(round(5000 * step_time)))
    sample_rate = sampel_per_bit / step_time
    if sample_rate > 100_000:
        raise ValueError("För kort bittid för denna DAQ.")

    avkodare = SynkadBitavkodare(sampel_per_bit, threshold)
    deadline = time.monotonic() + timeout_s
    print(f"Lyssnar på {channel}: {sample_rate:.0f} sampel/s")

    try:
        with nidaqmx.Task() as task:
            task.ai_channels.add_ai_voltage_chan(
                channel, terminal_config=TerminalConfiguration.RSE,
                min_val=-10.0, max_val=10.0,
            )
            task.timing.cfg_samp_clk_timing(
                rate=sample_rate,
                sample_mode=AcquisitionType.CONTINUOUS,
                samps_per_chan=int(sample_rate * 2),
            )
            task.in_stream.input_buf_size = int(sample_rate * 10)
            task.start()
            if ready_event is not None:
                ready_event.set()

            while time.monotonic() < deadline:
                block = task.read(
                    number_of_samples_per_channel=max(sampel_per_bit, int(sample_rate * 0.02)),
                    timeout=2.0,
                )
                avkodare.mata_in(block)
                if avkodare.klar:
                    print(f"Slutsekvens mottagen. {len(avkodare.payload)} nyttobitar.")
                    return avkodare.payload

    except Exception as exc:
        print(f"Mottagarfel: {exc}")
        return []
    finally:
        if ready_event is not None:
            ready_event.set()

    print("Timeout: ingen fullständig ram mottagen.")
    return []

if __name__ == "__main__":
    print("Mottagna nyttobitar:", receive_continuous())