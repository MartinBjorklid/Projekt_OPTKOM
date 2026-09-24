
import threading
import time
from skicka import send, step_time
from taemot import receive_continuous
from Signalbehandling import huffman_decode, tree


def main():
    text = input("Skriv ett meddelande: ")
    
    # Här sparar vi datan från bakgrundstråden
    motagna_bitar = []

    def lyssna_i_bakgrunden():
        nonlocal motagna_bitar
        # Mottagaren lyssnar tills den ser SLUT_SEQ, och returnerar då det rena meddelandet
        motagna_bitar = receive_continuous(step_time, channel="Dev1/ai0", threshold=3.0)

    # 1. Sätt igång mottagaren i bakgrunden
    mottagar_trad = threading.Thread(target=lyssna_i_bakgrunden)
    mottagar_trad.start()
    
    # 2. Låt DAQ-kortet "vakna" och börja sin kontinuerliga lyssning
    time.sleep(0.5)
    
    # 3. Skicka meddelandet (lasern börjar blinka!)
    print("\n[MAIN] Startar sändningen...")
    send(text)
    
    # 4. Invänta mottagaren. 
    # Denna rad pausar huvudprogrammet tills mottagaren har sett SLUT_SEQ 
    # och stängt av sig själv.
    mottagar_trad.join()

    # 5. Resultat!
    print("\n==============================")
    print("Mottagen ren data (start/stopp är bortklippt):")
    print(motagna_bitar)

    print("\nAvkodat ord:")
    if motagna_bitar:  # Kontrollera att vi faktiskt fångade något
        try:
            decoded = huffman_decode(motagna_bitar, tree)
            print(f"---> {decoded} <---")
            
            if decoded == text.lower():
                print("\nAvkodning stämmer")
            else:
                print("\nNågot blev fel")
                
        except Exception as e:
            print(f"Avkodningsfel: {e}")
    else:
        print("Ingen data mottogs. Fick mottagaren ljus på sig?")
    print("==============================")

if __name__ == "__main__":
    main()