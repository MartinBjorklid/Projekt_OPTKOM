"""Ersätt innehållet i program.py med denna fil när taemot_synk.py
har kopierats till taemot.py. skicka.py/Signalbehandling.py behålls.
"""

import threading
import time

from skicka import send, step_time, Start_seq, Slut_seq
from taemot import receive_continuous
from Signalbehandling import huffman_decode, huffman_encode, codes, tree


def main():
    text = input("Skriv ett meddelande: ").lower()
    nyttobitar = huffman_encode(text, codes)
    # Den befintliga slutsekvensen kan fortfarande förekomma INNE i andra meddelanden.
    for i in range(len(nyttobitar) - len(Slut_seq) + 1):
        if nyttobitar[i:i + len(Slut_seq)] == Slut_seq:
            print("Slutsekvensen förekommer i nyttodatan. Välj annat meddelande "
                  "för detta test, eller byt till ett protokoll med längdfält.")
            return

    mottagna_bitar = []
    redo = threading.Event()
    timeout_s = max(15.0, (len(Start_seq) + len(nyttobitar) + len(Slut_seq)) * step_time + 6)

    def lyssna():
        nonlocal mottagna_bitar
        mottagna_bitar = receive_continuous(
            step_time=step_time, channel="Dev1/ai0", threshold=2.3,
            timeout_s=timeout_s, ready_event=redo,
        )

    trad = threading.Thread(target=lyssna)
    trad.start()
    if not redo.wait(timeout=5.0):
        print("Mottagaren kunde inte startas. Avbryter sändningen.")
        trad.join(timeout=timeout_s + 3)
        return

    if not trad.is_alive():
        print("Mottagaren kunde inte startas. Kontrollera DAQ och felutskriften.")
        return

    # Liten viloperiod för att avkodaren ska hinna se signalnivån före start.
    time.sleep(0.2)
    print("\n[MAIN] Startar sändningen...")
    send(text)
    trad.join(timeout=timeout_s + 3)
    if trad.is_alive():
        print("Mottagaren avslutades inte inom tidsgränsen.")
        return

    print(f"\nMottagna nyttobitar: {len(mottagna_bitar)} / {len(nyttobitar)}")
    if not mottagna_bitar:
        print("Ingen fullständig ram mottagen.")
        return
    if len(mottagna_bitar) != len(nyttobitar):
        print("VARNING: Fel antal nyttobitar. Kontrollera synkronisering eller falsk slutsekvens.")
    try:
        avkodad = huffman_decode(mottagna_bitar, tree)
        print("Avkodad text:", avkodad)
        print("Stämmer med original:", avkodad == text)
    except Exception as exc:
        print(f"Avkodningen misslyckades: {exc}")


if __name__ == "__main__":
    main()
