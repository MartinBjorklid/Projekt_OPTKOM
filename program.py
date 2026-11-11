"""Ersätt innehållet i program.py med denna fil när taemot_synk.py
har kopierats till taemot.py. skicka.py/Signalbehandling.py behålls.
"""

import threading
import time

from parametrar import step_time, threshold
from skicka import send_binary_list, bygg_ram
from taemot import receive_continuous
from Signalbehandling import huffman_decode, huffman_encode, codes, tree


def main():
    text = input("Skriv ett meddelande: ").lower()
    nyttobitar = huffman_encode(text, codes)
    ram = bygg_ram(nyttobitar)

    mottagna_bitar = []
    redo = threading.Event()
    timeout_s = max(15.0, len(ram) * step_time + 6)

    def lyssna():
        nonlocal mottagna_bitar
        mottagna_bitar = receive_continuous(
            step_time=step_time, channel="Dev1/ai0", threshold=threshold,
            timeout_s=timeout_s, ready_event=redo, skickade_bitar=ram,
        )

    # daemon: programmet kan avslutas även om mottagaren väntar på ett felaktigt längdfält.
    trad = threading.Thread(target=lyssna, daemon=True)
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
    send_binary_list(ram)
    trad.join(timeout=timeout_s + 3)
    if trad.is_alive():
        print("Mottagaren avslutades inte inom tidsgränsen.")
        return

    print(f"\nMottagna nyttobitar: {len(mottagna_bitar)} / {len(nyttobitar)}")
    if not mottagna_bitar:
        print("Ingen fullständig ram mottagen. Kör python felsok.py för att se varför.")
        return
    if len(mottagna_bitar) != len(nyttobitar):
        print("VARNING: Fel antal nyttobitar. Längdfältet har troligen flera bitfel.")
    bitfel = sum(a != b for a, b in zip(mottagna_bitar, nyttobitar))
    print(f"Bitfel: {bitfel} av {min(len(mottagna_bitar), len(nyttobitar))}")
    if bitfel or len(mottagna_bitar) != len(nyttobitar):
        print("Kör python felsok.py för att se var och varför bitarna blev fel.")
    try:
        avkodad = huffman_decode(mottagna_bitar, tree)
        print("Avkodad text:", avkodad)
        print("Stämmer med original:", avkodad == text)
    except Exception as exc:
        print(f"Avkodningen misslyckades: {exc}")


if __name__ == "__main__":
    main()
