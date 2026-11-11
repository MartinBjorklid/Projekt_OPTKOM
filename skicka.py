import sys
#from unittest.mock import MagicMock

# Lura systemet att nidaqmx och dess konstanter är installerade
#sys.modules['nidaqmx'] = MagicMock()
#sys.modules['nidaqmx.constants'] = MagicMock()

import nidaqmx 
import nidaqmx.constants
from nidaqmx.constants import AcquisitionType
import matplotlib.pyplot as plt
from Signalbehandling import huffman_encode, codes,tree,huffman_decode
import parametrar
from parametrar import step_time, drivfrekvens
# Äldre parametrar.py saknar drivamplitud; då gäller den tidigare fasta ±5 V.
drivamplitud = getattr(parametrar, "drivamplitud", 5.0)

Start_seq = [1,1,1,1,0,0]
# Antal bitar i längdfältet efter startsekvensen. 16 bitar ger max 65535 nyttobitar.
LANGDFALT_BITAR = 16
# Längdfältet skickas så här många gånger; mottagaren tar majoritet per bit (udda tal).
LANGDFALT_KOPIOR = 3
AO_MAX_FREKVENS = 5000  # S/s, USB-6003
AO_MAX_SPANNING = 10.0  # V, USB-6003 ao0 har området ±10 V


def bygg_ram(nyttobitar):
    """Start_seq + längdfält x LANGDFALT_KOPIOR (mest signifikant bit först) + nyttobitar."""
    if len(nyttobitar) >= 2 ** LANGDFALT_BITAR:
        raise ValueError(f"Meddelandet är för långt: max {2 ** LANGDFALT_BITAR - 1} bitar. "
                         "Öka LANGDFALT_BITAR i skicka.py.")
    langd = [int(b) for b in format(len(nyttobitar), f"0{LANGDFALT_BITAR}b")]
    return Start_seq + langd * LANGDFALT_KOPIOR + list(nyttobitar)


def ao_sampel_per_bit(step_time, drivfrekvens=drivfrekvens):
    """AO-sampel per bit så att en etta (+5,-5,+5,... ett sampel var) ger drivfrekvensen.

    Drivfrekvens = sampel_per_bit / (2 * step_time). Begränsas av AO_MAX_FREKVENS.
    1 sampel per bit betyder att polariteten byts för varje etta i stället.
    """
    onskat = round(2 * drivfrekvens * step_time)
    return max(1, min(onskat, int(AO_MAX_FREKVENS * step_time)))


def ao_frekvens(step_time):
    """AO-samplingsfrekvens för given bittid."""
    return ao_sampel_per_bit(step_time) / step_time


def faktisk_bitfrekvens(step_time, channel="Dev1/ao0"):
    """Bitfrekvensen DAQ:n verkligen ger. DAQmx justerar begärd frekvens till
    närmaste möjliga, och mottagaren måste veta den exakta för att inte glida."""
    try:
        with nidaqmx.Task() as task:
            task.ao_channels.add_ao_voltage_chan(channel, min_val=-AO_MAX_SPANNING, max_val=AO_MAX_SPANNING)
            task.timing.cfg_samp_clk_timing(rate=ao_frekvens(step_time), samps_per_chan=2)
            return float(task.timing.samp_clk_rate) / ao_sampel_per_bit(step_time)
    except Exception:
        return 1.0 / step_time


def till_spanningar(values, step_time=step_time, drivfrekvens=drivfrekvens,
                    amplitud=drivamplitud):
    """0/1 -> AO-sampel. En etta blir ao_sampel_per_bit sampel med växlande
    ±amplitud, en nolla blir 0 V. Polariteten växlar bara på drivna sampel, så
    medelspänningen över LC-kristallen blir noll oavsett data."""
    if not 0 < amplitud <= AO_MAX_SPANNING:
        raise ValueError(f"Amplituden måste vara mellan 0 och {AO_MAX_SPANNING} V.")
    voltages = []
    polaritet = 1.0
    for value in values:
        for _ in range(ao_sampel_per_bit(step_time, drivfrekvens)):
            if value:
                voltages.append(amplitud * polaritet)
                polaritet = -polaritet
            else:
                voltages.append(0.0)
    return voltages

def send_binary_list(values, step_time=step_time, extra_time=1.0, channel="Dev1/ao0"):
    """
    Skickar en lista med 0/1 till NI USB-6003 med HÅRDVARUKLOCKA.
    """
   
    if not isinstance(values, list) or len(values) == 0:
        raise ValueError("Ogiltig lista")

    # -----------------------------
    # Konvertera 0/1 -> ±5 V-växelspänning för ettor, 0 V för nollor
    # -----------------------------
    voltages = till_spanningar(values, step_time)
    sample_rate = ao_frekvens(step_time)

    # För "extra_time", lägger vi helt enkelt till nollor i slutet av listan
    extra_samples = max(1, int(extra_time * sample_rate))
    voltages.extend([0.0] * extra_samples)

    # -----------------------------
    # Kör NI-DAQmx med hårdvarutajming
    # -----------------------------
    print(f"Skickar data med hastigheten {1.0 / step_time:g} bit/s "
          f"({sample_rate:g} AO-sampel/s, drivfrekvens {sample_rate / 2:g} Hz)...")

    try:
        with nidaqmx.Task() as task:
            task.ao_channels.add_ao_voltage_chan(
                channel,
                min_val=-AO_MAX_SPANNING,
                max_val=AO_MAX_SPANNING
            )

            # Säg åt DAQ-kortet att använda sin egen klocka för att skicka värdena
            task.timing.cfg_samp_clk_timing(
                rate=sample_rate,
                samps_per_chan=len(voltages)
            )

            # Skriv hela listan med spänningar till DAQ-kortets minne på en gång.
            # auto_start=True gör att den börjar spotta ut värden direkt i rätt takt.
            task.write(voltages, auto_start=True)

            # Istället för time.sleep(), låter vi programmet vänta tills 
            # hårdvaran rapporterar att sista värdet är skickat.
            timeout_sekunder = (len(voltages) / sample_rate) + 5.0
            task.wait_until_done(timeout=timeout_sekunder)
            
            print("Sändning klar!")

    finally:
        # Säkerhetsåterställning: Tvinga alltid utgången till 0V när vi är klara
        # Detta görs i en separat "software-timed" task för att garantera att det körs
        try:
            with nidaqmx.Task() as safety_task:
                safety_task.ao_channels.add_ao_voltage_chan(
                    channel, min_val=-AO_MAX_SPANNING, max_val=AO_MAX_SPANNING)
                safety_task.write(0.0)
        except Exception:
            pass

    plot_time = [i / sample_rate for i in range(len(voltages) + 1)]
    plot_voltage = voltages + [0.0]

    # -----------------------------
    # Plot
    # -----------------------------

    plt.figure(figsize=(12, 5))

    plt.plot(
        plot_time,
        plot_voltage,
        drawstyle="steps-post",
        linewidth=2
    )
    

    plt.xlabel("Tid [s]")
    plt.ylabel("Spänning [V]")
    plt.title("NI-DAQmx analog utgång")

    plt.ylim(-5.5, 5.5)
    plt.grid(True)

    plt.axhline(
        0,
        color="black",
        linewidth=0.8
    )

    plt.axhline(
        5,
        color="red",
        linestyle="--",
        linewidth=0.8,
        label="5 V"
    )

    plt.legend()
    plt.tight_layout()
    plt.show(block=False)
    plt.pause(2)


def send(text):

# Gör texten till små bokstäver
    text = text.lower()

    encoded = huffman_encode(text, codes)

    print()
    print("Originaltext:")
    print(text)

    print()
    print("Bit-kod:")
    print(encoded)

    fullt_medelanda = bygg_ram(encoded)
    print(fullt_medelanda)
    print()
    print ("Antal bitar komprimerat")
    print(len(encoded))
    print("Antal bitar att skicka:")
    print(len(fullt_medelanda))

    print() 
    print("Antal bitar okomprimetat")
    print(len(text)*8)
    
    return send_binary_list(fullt_medelanda)




if __name__ == "__main__":
    text = input("Skriv ett meddelande i main: ")
    send(text)
    

   



