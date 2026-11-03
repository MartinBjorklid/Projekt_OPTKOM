"""Mottagare med automatisk bitsynkronisering via startsekvensen.

Läser enbart Dev1/ai0 (TIA-signalen), inte hårdvarukomparatorn.
Kräver numpy och nidaqmx.

Ram: Start_seq | längdfält x LANGDFALT_KOPIOR | nyttobitar.
Mottagaren synkar på startsekvensens första stigande flank och följer sedan
sändarens klocka genom att justera samplingen vid varje stigande flank.
Varje mottagning sparas i senaste_mottagning.npz; kör felsok.py för att se den.
"""

import time

import numpy as np

import felsok
from skicka import Start_seq, LANGDFALT_BITAR, LANGDFALT_KOPIOR, faktisk_bitfrekvens
from parametrar import step_time as STANDARD_BITTID, threshold as STANDARD_TROSKEL

# Hur stor del av flankfelet som korrigeras vid varje stigande flank (0–1).
FASFORSTARKNING = 0.5
# Fler oeniga bitpositioner än så här mellan längdfältets kopior = falsk synk.
MAX_OENIGA_LANGDBITAR = 3


def samplingsparametrar(step_time):
    """Returnera (sampel_per_bit, sample_rate). Används även av kalibrering.py."""
    if step_time <= 0:
        raise ValueError("step_time måste vara positiv.")
    # Minst 25 sampel/bit, utan att överstiga 100 kS/s för en AI-kanal.
    sampel_per_bit = max(25, int(round(5000 * step_time)))
    sample_rate = sampel_per_bit / step_time
    if sample_rate > 100_000:
        raise ValueError("För kort bittid för denna DAQ och vald översampling.")
    return sampel_per_bit, sample_rate


class SynkadBitavkodare:
    """Tar emot en ström av AI-sampel och avläser bitar i deras mitt.

    sampel_per_bit får vara ett decimaltal (AI-frekvens / faktisk bitfrekvens).
    """

    def __init__(self, sampel_per_bit, threshold=STANDARD_TROSKEL, tyst=False):
        self.n = float(sampel_per_bit)
        if self.n < 12:
            raise ValueError("För få AI-sampel per bit; öka AI-samplingsfrekvensen.")
        self.threshold = float(threshold)
        self.tyst = tyst
        self.data = []
        self.sok_index = 1
        self.klar = False
        self._nollstall_ram()

    def _nollstall_ram(self):
        self.start_index = None
        self.bitstart = None       # sampel där nästa bit börjar (decimaltal)
        self.forra_bit = 0
        self.langdbitar = []
        self.langd = None          # antal nyttobitar enligt längdfältet
        self.payload = []
        # För felsökning: alla avlästa bitar inklusive startsekvens och längdfält.
        self.bitpositioner = []
        self.medianer = []
        self.fasfel = []           # (bitnummer, flankfel i sampel)

    def _skriv(self, text):
        if not self.tyst:
            print(text)

    def _median(self, pos):
        # Använd BARA mitten-tredjedelen av biten; undvik båda flankerna.
        a = int(round(pos + self.n / 3))
        b = int(round(pos + 2 * self.n / 3))
        return float(np.median(self.data[a:b]))

    def mata_in(self, sampel):
        if self.klar:
            return
        self.data.extend(sampel)
        while not self.klar:
            if self.start_index is None and not self._hitta_start():
                return
            if self._las_bitar():
                return  # behöver mer data

    def _hitta_start(self):
        # Identifiera första stigande flanken efter vilonivå.
        # Prova kandidater först när HELA startsekvensen finns i bufferten.
        startlangd = int(np.ceil(len(Start_seq) * self.n)) + 1
        while self.sok_index + startlangd <= len(self.data):
            i = self.sok_index
            self.sok_index += 1
            if not (self.data[i - 1] < self.threshold <= self.data[i]):
                continue
            # Kräv en låg nivå före den första ettan i startsekvensen.
            if i >= self.n and np.median(self.data[i - int(self.n):i]) >= self.threshold:
                continue
            positioner = [i + j * self.n for j in range(len(Start_seq))]
            medianer = [self._median(p) for p in positioner]
            if [int(m >= self.threshold) for m in medianer] == Start_seq:
                self.start_index = i
                self.bitpositioner = positioner
                self.medianer = medianer
                self.bitstart = i + len(Start_seq) * self.n
                self.forra_bit = Start_seq[-1]
                self._skriv(f"Startsekvens bekräftad vid AI-sampel {i}. "
                            "Synkroniserar till bitarnas mittpunkter.")
                return True
        return False

    def _las_bitar(self):
        """Läs bitar så länge data finns. True = behöver mer data, False = ramen klar
        eller förkastad (då söks en ny startsekvens)."""
        halv = self.n / 2
        # Hela biten (plus marginal för flanksökningen) ska finnas i bufferten.
        while self.bitstart + self.n + 1 <= len(self.data):
            pos = self.bitstart
            median = self._median(pos)
            bit = int(median >= self.threshold)

            # Klockföljning: vid 0 -> 1 letar vi upp den stigande flanken nära den
            # förväntade bitgränsen och flyttar samplingen en del av vägen mot den.
            # Bara stigande flanker används: de är snabba och samma typ som startsynken.
            if bit == 1 and self.forra_bit == 0:
                a = max(1, int(pos - halv))
                seg = np.asarray(self.data[a - 1:int(pos + halv)])
                korsningar = np.flatnonzero((seg[:-1] < self.threshold) & (seg[1:] >= self.threshold))
                if korsningar.size:
                    flankfel = (a + korsningar[0]) - pos
                    self.fasfel.append((len(self.bitpositioner), flankfel))
                    pos += FASFORSTARKNING * flankfel
                    median = self._median(pos)
                    bit = int(median >= self.threshold)

            self.bitpositioner.append(pos)
            self.medianer.append(median)
            self.bitstart = pos + self.n
            self.forra_bit = bit
            if not self._ta_emot_bit(bit):
                return False
        return True

    def _ta_emot_bit(self, bit):
        """Först längdfältet (alla kopior), sedan exakt så många nyttobitar som det anger."""
        if self.langd is None:
            self.langdbitar.append(bit)
            if len(self.langdbitar) < LANGDFALT_KOPIOR * LANGDFALT_BITAR:
                return True
            kopior = np.array(self.langdbitar).reshape(LANGDFALT_KOPIOR, LANGDFALT_BITAR)
            majoritet = (2 * kopior.sum(axis=0) > LANGDFALT_KOPIOR).astype(int)
            oeniga = int(np.sum(kopior.min(axis=0) != kopior.max(axis=0)))
            langd = int("".join(map(str, majoritet)), 2)
            if langd == 0 or oeniga > MAX_OENIGA_LANGDBITAR:
                self._skriv(f"Ogiltigt längdfält (längd {langd}, {oeniga} oeniga bitar) – "
                            "troligen falsk synk. Söker ny startsekvens.")
                self.sok_index = self.start_index + 1
                self._nollstall_ram()
                return False
            self.langd = langd
            self._skriv(f"Längdfält mottaget: {langd} nyttobitar"
                        + (f" ({oeniga} bitfel rättade med majoritet)." if oeniga else "."))
        else:
            self.payload.append(bit)

        if len(self.payload) == self.langd:
            self.klar = True
            return False
        return True


def receive_continuous(step_time=STANDARD_BITTID, channel="Dev1/ai0",
                       threshold=STANDARD_TROSKEL, timeout_s=15.0, ready_event=None,
                       skickade_bitar=None, stop_event=None, avkodare_ut=None, kasta_fel=False):
    """Returnera nyttobitar eller [] vid timeout. Samplar med DAQ:ens hårdvaruklocka.

    ready_event kan användas av program.py så att sändning sker först efter
    att DAQ-mottagningen faktiskt startat. skickade_bitar (hela ramen) sparas
    med mottagningen så att felsok.py kan markera felaktiga bitar.

    För gränssnittet: stop_event avbryter mottagningen, avkodaren läggs i listan
    avkodare_ut (så att data kan visas medan den tas emot) och kasta_fel=True
    skickar vidare fel i stället för att bara skriva ut dem.
    """
    import nidaqmx
    from nidaqmx.constants import AcquisitionType, TerminalConfiguration

    _, sample_rate = samplingsparametrar(step_time)
    bitfrekvens = faktisk_bitfrekvens(step_time)
    avkodare = None
    deadline = time.monotonic() + timeout_s
    deadline_forlangd = False

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
            # DAQmx kan ha justerat frekvensen; räkna med den verkliga.
            sample_rate = float(task.timing.samp_clk_rate)
            avkodare = SynkadBitavkodare(sample_rate / bitfrekvens, threshold)
            avkodare.ai_rate = sample_rate
            if avkodare_ut is not None:
                avkodare_ut.append(avkodare)
            print(f"Lyssnar på {channel}: {sample_rate:.1f} sampel/s, "
                  f"{avkodare.n:.3f} sampel/bit, tröskel {threshold:g} V.")
            task.in_stream.input_buf_size = int(sample_rate * 10)
            task.start()
            if ready_event is not None:
                ready_event.set()

            while time.monotonic() < deadline:
                if stop_event is not None and stop_event.is_set():
                    print("Mottagning avbruten.")
                    break
                block = task.read(
                    number_of_samples_per_channel=max(int(avkodare.n), int(sample_rate * 0.02)),
                    timeout=2.0,
                )
                avkodare.mata_in(block)
                if avkodare.klar:
                    print(f"Hela meddelandet mottaget. {len(avkodare.payload)} nyttobitar.")
                    return avkodare.payload
                # När längden är känd: vänta minst så länge resten av meddelandet tar.
                if avkodare.langd is not None and not deadline_forlangd:
                    deadline = max(deadline, time.monotonic() + avkodare.langd / bitfrekvens + 3.0)
                    deadline_forlangd = True

    except Exception as exc:
        print(f"Mottagarfel: {exc}")
        if kasta_fel:
            raise
        return []
    finally:
        # Väck huvudprogrammet även om NI-task inte kunde startas.
        if ready_event is not None:
            ready_event.set()
        if avkodare is not None:
            try:
                felsok.spara(felsok.FELSOKFIL, avkodare, sample_rate, skickade_bitar)
            except Exception as exc:
                print(f"Kunde inte spara felsökningsdata: {exc}")

    print("Timeout: ingen fullständig ram mottagen.")
    return []


if __name__ == "__main__":
    print("Mottagna nyttobitar:", receive_continuous())
