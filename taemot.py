"""Synkron mottagare för 2/4/8 nivåer på TIA-signalen (Dev1/ai0)."""

import time

import numpy as np

from Signalbehandling import (START_BITS as Start_seq, STOP_BITS as Slut_seq,
    validate_thresholds, validate_step_time, bits_per_symbol, symbols_to_bits,
    communication_args)
STANDARD_BITTID = 0.004


class SynkadBitavkodare:
    """Tar emot en ström av AI-sampel och avläser bitar i deras mitt."""

    def __init__(self, sampel_per_bit, threshold=None, max_payload_bits=4096, levels=2):
        self.n = int(sampel_per_bit)
        if self.n < 12:
            raise ValueError("För få AI-sampel per bit; öka AI-samplingsfrekvensen.")
        self.levels = levels
        self.width = bits_per_symbol(levels)
        self.thresholds = validate_thresholds(threshold, levels)
        self.threshold = self.thresholds[(levels-2)//2]
        self.start_symbols = [b * (levels-1) for b in Start_seq]
        self.stop_symbols = [b * (levels-1) for b in Slut_seq]
        self.symbols = []
        self.payload_length = None
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
        return int(np.searchsorted(self.thresholds, np.median(self.data[a:b]), side="right"))

    def mata_in(self, sampel):
        if self.klar:
            return
        self.data.extend(sampel)
        startlangd = len(Start_seq) * self.n

        if self.start_index is None:
            # Identifiera första stigande flanken efter vilonivå.
            # Prova kandidater först när HELA startsekvensen finns i bufferten.
            while self.sok_index + startlangd <= len(self.data):
                i = self.sok_index
                self.sok_index += 1
                if not (self.data[i - 1] < self.threshold <= self.data[i]):
                    continue
                # Kräv en låg nivå före den första ettan i startsekvensen.
                if i >= self.n and np.median(self.data[i-self.n:i]) >= self.threshold:
                    continue
                if [self._bit(i, j) for j in range(len(Start_seq))] == self.start_symbols:
                    self.start_index = i
                    print(f"Startsekvens bekräftad vid AI-sampel {i}. "
                          "Synkroniserar till bitarnas mittpunkter.")
                    break

        if self.start_index is None:
            return

        # Läs varje efterföljande bit relativt den upptäckta startflanken.
        # Hela biten ska finnas i bufferten innan den avläses.
        while self.start_index + (self.nasta_bit + 1) * self.n <= len(self.data):
            bit = self._bit(self.start_index, self.nasta_bit)
            self.nasta_bit += 1
            if self.levels > 2:
                self._multilevel_symbol(bit)
                if self.klar:
                    return
                continue
            self.payload.append(bit)

            if len(self.payload) >= len(Slut_seq) and self.payload[-len(Slut_seq):] == Slut_seq:
                self.payload = self.payload[:-len(Slut_seq)]
                self.klar = True
                return

            if len(self.payload) > self.max_payload_bits + len(Slut_seq):
                raise RuntimeError("Meddelandet överskred max_payload_bits utan slutsekvens.")

    def _multilevel_symbol(self, symbol):
        self.symbols.append(symbol)
        if len(self.symbols) == 32:
            if any(s not in (0, self.levels-1) for s in self.symbols):
                raise ValueError("Ogiltig nivå i längdfältet.")
            header = [int(s == self.levels-1) for s in self.symbols]
            if header[16:] != [1-b for b in header[:16]]:
                raise ValueError("Skadat längdfält (inversen stämmer inte).")
            self.payload_length = int("".join(map(str, header[:16])), 2)
            if self.payload_length > self.max_payload_bits:
                raise ValueError("Meddelandet överskrider max_payload_bits.")
        if self.payload_length is None:
            return
        count = (self.payload_length + self.width - 1) // self.width
        if len(self.symbols) == 32 + count + len(self.stop_symbols):
            if self.symbols[-len(self.stop_symbols):] != self.stop_symbols:
                raise ValueError("Ogiltig slutsekvens.")
            bits = symbols_to_bits(self.symbols[32:32+count], self.levels)
            if any(bits[self.payload_length:]):
                raise ValueError("Ogiltig symbolutfyllnad.")
            self.payload = bits[:self.payload_length]
            self.klar = True


def receive_continuous(step_time=STANDARD_BITTID, channel="Dev1/ai0",
                       threshold=None, timeout_s=15.0, ready_event=None, levels=2):
    """Returnera nyttobitar eller [] vid timeout. Samplar med DAQ:ens hårdvaruklocka.

    ready_event kan användas av program.py så att sändning sker först efter
    att DAQ-mottagningen faktiskt startat.
    """
    import nidaqmx
    from nidaqmx.constants import AcquisitionType, TerminalConfiguration

    validate_step_time(step_time)
    # Minst 25 sampel/bit, utan att överstiga 100 kS/s för en AI-kanal.
    sampel_per_bit = max(25, int(round(5000 * step_time)))
    sample_rate = sampel_per_bit / step_time
    if sample_rate > 100_000:
        raise ValueError("För kort bittid för denna DAQ och vald översampling.")

    avkodare = SynkadBitavkodare(sampel_per_bit, threshold, levels=levels)
    deadline = time.monotonic() + timeout_s
    print(f"Lyssnar på {channel}: {sample_rate:.0f} sampel/s, "
          f"{sampel_per_bit} sampel/symbol, {levels} nivåer, trösklar {avkodare.thresholds} V.")

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
        # Väck huvudprogrammet även om NI-task inte kunde startas.
        if ready_event is not None:
            ready_event.set()

    print("Timeout: ingen fullständig ram mottagen.")
    return []


if __name__ == "__main__":
    args = communication_args(description="Ta emot optiskt meddelande")
    print("Mottagna nyttobitar:", receive_continuous(
        step_time=args.step_time, threshold=args.threshold, levels=args.levels))
