import math

import matplotlib.pyplot as plt
from Signalbehandling import (huffman_encode, codes, frame_symbols, symbol_voltages,
                              validate_step_time, communication_args,
                              START_BITS, STOP_BITS)

Start_seq = START_BITS
Slut_seq = STOP_BITS
step_time = 0.004

def send_symbols(values, step_time=step_time, extra_time=1.0, channel="Dev1/ao0", levels=2):
    """
    Skickar en lista med symboler till NI USB-6003 med HÅRDVARUKLOCKA.
    """
   
    import nidaqmx

    if not isinstance(values, list) or len(values) == 0:
        raise ValueError("Ogiltig lista")

    # -----------------------------
    # Konvertera symbol -> 0...5 V (med alternerande polaritet för LC-kristallen)
    # -----------------------------
    validate_step_time(step_time)
    if not math.isfinite(extra_time) or extra_time < 0:
        raise ValueError("extra_time måste vara ändlig och icke-negativ.")
    voltages = symbol_voltages(values, levels)
    
    # För "extra_time", lägger vi helt enkelt till nollor i slutet av listan
    # Vi räknar ut hur många "steg" som ryms i extra_time
    extra_samples = max(1, int(extra_time / step_time))
    voltages.extend([0.0] * extra_samples)

    # -----------------------------
    # Kör NI-DAQmx med hårdvarutajming
    # -----------------------------
    sample_rate = 1.0 / step_time
    
    print(f"Skickar data med hastigheten {sample_rate} symboler/sekund...")

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

    plt.legend()
    plt.tight_layout()
    plt.show(block=False)
    plt.pause(2)


def send_binary_list(values, step_time=step_time, extra_time=1.0, channel="Dev1/ao0"):
    """Bakåtkompatibel rå binär sändare (ramen ingår i values)."""
    return send_symbols(values, step_time, extra_time, channel, levels=2)


def send_payload(bits, step_time=step_time, levels=2, channel="Dev1/ao0"):
    return send_symbols(frame_symbols(bits, levels), step_time=step_time,
                        levels=levels, channel=channel)


def send(text, step_time=step_time, levels=2):

# Gör texten till små bokstäver
    text = text.lower()

    encoded = huffman_encode(text, codes)

    print()
    print("Originaltext:")
    print(text)

    print()
    print("Bit-kod:")
    print(encoded)

    fullt_medelanda = frame_symbols(encoded, levels)
    print(fullt_medelanda)
    print()
    print ("Antal bitar komprimerat")
    print(len(encoded))
    print("Antal symboler att skicka:")
    print(len(fullt_medelanda))

    print() 
    print("Antal bitar okomprimetat")
    print(len(text)*8)
    
    return send_symbols(fullt_medelanda, step_time=step_time, levels=levels)




if __name__ == "__main__":
    args = communication_args(description="Skicka optiskt meddelande", receiving=False)
    text = input("Skriv ett meddelande: ")
    send(text, step_time=args.step_time, levels=args.levels)
