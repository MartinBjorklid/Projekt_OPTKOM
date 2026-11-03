import sys
import time
import matplotlib.pyplot as plt
from Signalbehandling import huffman_encode, codes, tree, huffman_decode

Start_seq = [1, 1, 1, 1, 0, 0]
Slut_seq = [1, 0, 1, 1, 1, 0, 0, 1, 1, 0, 1, 1, 1]
step_time = 0.004

AVSLUTNINGSBIT = 0

def lagg_till_avslutningsbit(nyttobitar):
    """Lägger till avslutningsbiten efter Hamming-kodad nyttodata. Mottagaren tar bort den."""
    n = len(Slut_seq)
    if any(nyttobitar[i:i + n] == Slut_seq for i in range(len(nyttobitar) - n + 1)):
        # Kan bara hända om slutsekvensen eller Hamming-koden har ändrats
        raise ValueError("Slutsekvensen förekommer i nyttodatan. Välj en annan slutsekvens.")
    return list(nyttobitar) + [AVSLUTNINGSBIT]

def send_binary_list(values, step_time=step_time, extra_time=1.0, channel="Dev1/ao0", stop_event=None):
    """
    Skickar en lista med 0/1 till NI USB-6003 med HÅRDVARUKLOCKA.
    Om stop_event sätts avbryts sändningen och utgången nollställs.
    """
    # Lokal import: Kraschar inte om man kör i simuleringsläget utan nidaqmx installerat
    import nidaqmx 

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

            # Vänta i korta steg i stället för wait_until_done, så att sändningen
            # kan avbrytas (t.ex. om gränssnittet laddas om mitt i en överföring)
            timeout_sekunder = (len(voltages) * step_time) + 5.0
            deadline = time.monotonic() + timeout_sekunder
            while not task.is_task_done():
                if stop_event is not None and stop_event.is_set():
                    task.stop()
                    print("Sändning avbruten!")
                    return
                if time.monotonic() > deadline:
                    raise TimeoutError("Sändningen blev inte klar i tid.")
                time.sleep(0.05)
            
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