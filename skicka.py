import sys
from unittest.mock import MagicMock

# Lura systemet att nidaqmx och dess konstanter är installerade
sys.modules['nidaqmx'] = MagicMock()
sys.modules['nidaqmx.constants'] = MagicMock()

import nidaqmx 
import nidaqmx.constants
from nidaqmx.constants import AcquisitionType
import matplotlib.pyplot as plt
from Signalbehandling import huffman_encode, codes, tree, huffman_decode

Start_seq = [1, 1, 1, 1, 0, 0]
Slut_seq = [1, 0, 1, 1, 1, 0, 0, 1, 1, 0, 1, 1, 1]
step_time = 0.004

def send_binary_list(values, step_time=step_time, extra_time=1.0, channel="Dev1/ao0"):
    """
    Skickar en lista med 0/1 till NI USB-6003 med HÅRDVARUKLOCKA.
    """
    if not isinstance(values, list) or len(values) == 0:
        raise ValueError("Ogiltig lista")

    # Konvertera 0/1 -> 0/5 V (med alternerande polaritet för LC-kristallen)
    voltages = [value * 5.0 * ((-1) ** i) for i, value in enumerate(values)]
    
    # För "extra_time", lägger vi helt enkelt till nollor i slutet av listan
    extra_samples = max(1, int(extra_time / step_time))
    voltages.extend([0.0] * extra_samples)

    sample_rate = 1.0 / step_time
    print(f"Skickar data med hastigheten {sample_rate} Hz (bits/sekund)...")

    try:
        with nidaqmx.Task() as task:
            task.ao_channels.add_ao_voltage_chan(
                channel,
                min_val=-5.0,
                max_val=5.0
            )

            task.timing.cfg_samp_clk_timing(
                rate=sample_rate,
                samps_per_chan=len(voltages)
            )

            task.write(voltages, auto_start=True)

            timeout_sekunder = (len(voltages) * step_time) + 5.0
            task.wait_until_done(timeout=timeout_sekunder)
            
            print("Sändning klar!")

    finally:
        # Säkerhetsåterställning
        try:
            with nidaqmx.Task() as safety_task:
                safety_task.ao_channels.add_ao_voltage_chan(
                    channel, min_val=-5.0, max_val=5.0)
                safety_task.write(0.0)
        except Exception:
            pass

    # Matplotlib blockeras för att fungera med Streamlit!
    # plt.show(block=False)
    # plt.pause(2)

def send(text):
    text = text.lower()
    encoded = huffman_encode(text, codes)

    print(f"\nOriginaltext: {text}")
    print(f"Bit-kod: {encoded}")

    fullt_medelanda = Start_seq + encoded + Slut_seq
    print(fullt_medelanda)
    
    print(f"\nAntal bitar komprimerat: {len(encoded)}")
    print(f"Antal bitar att skicka: {len(fullt_medelanda)}")
    print(f"Antal bitar okomprimerat: {len(text)*8}\n")
    
    return send_binary_list(fullt_medelanda)

if __name__ == "__main__":
    text = input("Skriv ett meddelande i main: ")
    send(text)