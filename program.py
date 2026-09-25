"""Huffman och Hamming över optisk länk med valbara 2, 4 eller 8 nivåer."""

import threading
import time

from skicka import send_payload
from taemot import receive_continuous
from Signalbehandling import (huffman_decode, huffman_encode, encode, decode, codes, tree,
                              communication_args, frame_symbols)


def main(argv=None):
    args = communication_args(argv, "Optisk kommunikation med 2, 4 eller 8 nivåer")
    text = input("Skriv ett meddelande: ").lower()
    komprimerade_bitar = huffman_encode(text, codes)
    kodade_bitar, H, padding = encode(komprimerade_bitar)
    nyttobitar = kodade_bitar.tolist()
    
    ram = frame_symbols(nyttobitar, args.levels)

    mottagna_bitar = []
    redo = threading.Event()
    mottagarfel = []
    timeout_s = max(15.0, len(ram) * args.step_time + 6)

    def lyssna():
        nonlocal mottagna_bitar
        try:
            mottagna_bitar = receive_continuous(
                step_time=args.step_time, channel="Dev1/ai0", threshold=args.threshold,
                levels=args.levels, timeout_s=timeout_s, ready_event=redo,
            )
        except Exception as exc:
            mottagarfel.append(str(exc))
            redo.set()


    trad = threading.Thread(target=lyssna)
    trad.start()
    if not redo.wait(timeout=5.0):
        print("Mottagaren kunde inte startas. Avbryter sändningen.")
        trad.join(timeout=timeout_s + 3)
        return

    if not trad.is_alive():
        print("Mottagaren kunde inte startas. Kontrollera DAQ och felutskriften.", *mottagarfel)
        return

    # Liten viloperiod för att avkodaren ska hinna se signalnivån före start.
    time.sleep(0.2)
    print("\n[MAIN] Startar sändningen...")
    try:
        send_payload(nyttobitar, step_time=args.step_time, levels=args.levels)
    finally:
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
        avkodade_bitar = decode(mottagna_bitar, H, padding)
        avkodad = huffman_decode(avkodade_bitar, tree)
        print("Avkodad text:", avkodad)
        print("Stämmer med original:", avkodad == text)
    except Exception as exc:
        print(f"Avkodningen misslyckades: {exc}")


if __name__ == "__main__":
    main()
