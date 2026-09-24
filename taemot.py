import sys
from unittest.mock import MagicMock

import matplotlib.pyplot as plt
import time

from skicka import Start_seq, Slut_seq

import nidaqmx
import nidaqmx.constants
from nidaqmx.constants import AcquisitionType


def receive_continuous(step_time=0.1, channel="Dev1/ai0", threshold=1.5):
    oversample_factor = 10
    sample_rate = (1.0 / step_time) * oversample_factor
    samples_per_bit = int(sample_rate * step_time)

    print("Mottagaren är igång och väntar på startsekvens...")

    bit_buffer = []
    payload = []
    recording = False

    try:
        with nidaqmx.Task() as task:
            task.ai_channels.add_ai_voltage_chan(
                channel, min_val=0.0, max_val=5.0
            )
            
            # Sätt kortet i kontinuerligt läge (lyssnar tills vi säger stopp)
            task.timing.cfg_samp_clk_timing(
                rate=sample_rate,
                sample_mode=AcquisitionType.CONTINUOUS
            )

            # Loopa i oändlighet (tills vi avbryter med 'break')
            while True:
                # Läs exakt 0.1 sekunders data
                chunk = task.read(number_of_samples_per_channel=samples_per_bit)
                avg_voltage = sum(chunk) / len(chunk)
                
                # Tolka som 1 eller 0
                bit = 1 if avg_voltage >= threshold else 0

                if not recording:
                    bit_buffer.append(bit)
                    # Håll bufferten lika lång som startsekvensen för att spara minne
                    if len(bit_buffer) > len(Start_seq):
                        bit_buffer.pop(0)

                    # Känner vi igen startmönstret?
                    if bit_buffer == Start_seq:
                        print("Startsekvens upptäckt! Spelar in meddelande...")
                        recording = True
                        payload = [] # Börja spela in
                
                else:
                    payload.append(bit)
                    
                    # Kolla de sista bitarna i payloaden om de matchar STOP_SEQ
                    if len(payload) >= len(Slut_seq):
                        if payload[-len(Slut_seq):] == Slut_seq:
                            print("Slutsekvens upptäckt! Stänger av lyssningen.")
                            
                            # Klipp bort STOP_SEQ från själva meddelandet
                            final_data = payload[:-len(Slut_seq)]
                            return final_data
                            
    except Exception as e:
        print(f"Ett DAQ-fel uppstod: {e}")
        return []

if __name__ == "__main__":
    # Exempel: Lyssna i 5 sekunder med samma step_time som sändaren
    received_bits = receive_continuous()
    
    print("\nMottagen binär lista:")
    print(received_bits)
    print(f"Antal mottagna bitar: {len(received_bits)}")
    
    
    # Här kan du sedan skicka in received_bits till din avkodare:
   