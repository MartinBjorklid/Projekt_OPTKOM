import sys
from unittest.mock import MagicMock

# Lura systemet att nidaqmx och dess konstanter är installerade
sys.modules['nidaqmx'] = MagicMock()
sys.modules['nidaqmx.constants'] = MagicMock()

import nidaqmx 

import matplotlib.pyplot as plt
from Signalbehandling import huffman_encode, codes, tree, huffman_decode

Start_seq = [1, 1, 1, 1, 0, 0]
Slut_seq = [0, 0, 1, 1, 1, 1]

def send_binary_list(values, step_time=0.1, extra_time=1.0, channel="Dev1/ao0"):
    """
    Skickar en lista med 0/1 till NI USB-6003 med HÅRDVARUKLOCKA.
    """
   
    if not isinstance(values, list) or len(values) == 0:
        raise ValueError("Ogiltig lista")

    # -----------------------------
    # Konvertera 0/1 -> 0/5 V (med alternerande polaritet för LC-kristallen)
    # -----------------------------
    voltages = [value * 5.0 * ((-1) ** i) for i, value in enumerate(values)]
    
    # För "extra_time", lägger vi helt enkelt till nollor i slutet av listan
    # Vi räknar ut hur många "steg" som ryms i extra_time
    extra_samples = max(1, int(extra_time / step_time))
    voltages.extend([0.0] * extra_samples)

    # -----------------------------
    # Kör NI-DAQmx med hårdvarutajming
    # -----------------------------
    sample_rate = 1.0 / step_time
    
    print(f"Skickar data med hastigheten {sample_rate} Hz (bits/sekund)...")

    try:
        with nidaqmx.Task() as task:
            task.ao_channels.add_ao_voltage_chan(
                channel,
                min_val=-5.0,
                max_val=5.0
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
            timeout_sekunder = (len(voltages) * step_time) + 5.0
            task.wait_until_done(timeout=timeout_sekunder)
            
            print("Sändning klar!")

    finally:
        # Säkerhetsåterställning: Tvinga alltid utgången till 0V när vi är klara
        # Detta görs i en separat "software-timed" task för att garantera att det körs
        try:
            with nidaqmx.Task() as safety_task:
                safety_task.ao_channels.add_ao_voltage_chan(
                    channel, min_val=-5.0, max_val=5.0)
                safety_task.write(0.0)
        except Exception:
            pass

    plot_time = [i * step_time for i in range(len(voltages) + 1)]
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

    # Vi stänger av matplotlib-fönstret så att Streamlit får sköta graferna ostört!
    # plt.legend()
    # plt.tight_layout()
    # plt.show(block=False)
    # plt.pause(5)

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

    fullt_medelanda = Start_seq + encoded + Slut_seq
    print(fullt_medelanda)
    print()
    print("Antal bitar komprimerat")
    print(len(encoded))
    print("Antal bitar att skicka:")
    print(len(fullt_medelanda))

    print() 
    print("Antal bitar okomprimerat")
    print(len(text)*8)
    
    return send_binary_list(fullt_medelanda)

if __name__ == "__main__":
    text = input("Skriv ett meddelande i main: ")
    send(text)