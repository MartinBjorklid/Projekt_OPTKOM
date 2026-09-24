import streamlit as st
import numpy as np
import pandas as pd
import threading
import time

# STREAMLIT_CHUNK:Importerar backend-funktioner
# Importerar de faktiska hårdvarufunktionerna från projektets stödfiler
from skicka import send_binary_list, Start_seq, Slut_seq
from taemot import receive_continuous
from Signalbehandling import huffman_encode, huffman_decode, tree, codes

# ==============================================================================
# ANVÄNDARGRÄNSSNITT
# ==============================================================================
st.set_page_config(page_title="Optisk Kommunikation", layout="wide")

# STREAMLIT_CHUNK:Initierar state
if 'history' not in st.session_state:
    st.session_state.history = None

st.title("Optisk Kommunikation - Kontrollpanel")
st.markdown("---")

col_tx, col_rx = st.columns(2)

# STREAMLIT_CHUNK:Bygger sändarsidan
with col_tx:
    st.header("Sändare (LC-cell)")
    message_input = st.text_input("Mata in meddelande att skicka:", placeholder="Skriv ditt ord här...")
    
    if st.button("Skicka Data", use_container_width=True):
        if message_input:
            with st.spinner("Modulerar och sänder signal..."):
                text = message_input.lower()
                
                # 1. Huffman-koda meddelandet via Signalbehandling.py
                try:
                    encoded_bits = huffman_encode(text, codes)
                    tx_bits_full = Start_seq + encoded_bits + Slut_seq
                except Exception as e:
                    st.error(f"Kunde inte koda texten. Finns tecknen i tabellen? Fel: {e}")
                    st.stop()
                
                # Beräkna styrsignalen (bipolär 5V alternerande) för grafen i gränssnittet
                tx_voltage = [val * 5.0 * ((-1) ** i) for i, val in enumerate(tx_bits_full)]
                
                # 2. Starta mottagaren i bakgrunden (Tråd)
                # STREAMLIT_CHUNK:Hanterar trådning för mottagare
                motagna_resultat = []
                def lyssna_i_bakgrunden():
                    # Vi lägger till resultatet i listan istället för att skriva över en variabel
                    res = receive_continuous(step_time=0.1, channel="Dev1/ai0", threshold=1.5)
                    motagna_resultat.append(res)

                mottagar_trad = threading.Thread(target=lyssna_i_bakgrunden)
                mottagar_trad.start()
                
                # 3. Ge DAQ-kortets mottagare tid att vakna (exakt som i gamla main.py)
                time.sleep(0.5)
                
                # 4. Skicka signalen via hårdvaran (skicka.py)
                try:
                    send_binary_list(tx_bits_full, step_time=0.1, extra_time=1.0, channel="Dev1/ao0")
                except Exception as e:
                    st.error(f"DAQ-fel (Sändare): {e}")
                
                # 5. Invänta mottagaren (Pausar gränssnittet tills SLUT_SEQ hittats)
                mottagar_trad.join()
                
                # 6. Avkoda mottagen signal
                # STREAMLIT_CHUNK:Avkodar data
                rx_bits = motagna_resultat[0] if motagna_resultat else []
                if rx_bits:
                    try:
                        rx_text = huffman_decode(rx_bits, tree)
                    except Exception as e:
                        rx_text = f"Något blev fel vid avkodning: {e}"
                else:
                    rx_text = "[Ingen data mottogs. Fick mottagaren ljus på sig?]"
                
                # Eftersom taemot.py bara returnerar bitar och inte de analoga spänningsmätningarna, 
                # skapar vi en idealiserad representation för gränssnittets graf.
                rx_voltage = [val * 5.0 for val in rx_bits]
                
                # Spara all insamlad data till session state
                st.session_state.history = {
                    "original_text": text,
                    "tx_bits": tx_bits_full,
                    "tx_voltage": tx_voltage,
                    "rx_voltage": rx_voltage,
                    "rx_bits": rx_bits,
                    "rx_text": rx_text
                }
        else:
            st.warning("Vänligen mata in ett meddelande först.")

    # STREAMLIT_CHUNK:Renderar grafer för sändare
    if st.session_state.history:
        st.subheader("Skickade Bitar (inkl. start/stopp)")
        st.code(st.session_state.history["tx_bits"])
        st.subheader("Styrsignal till LC-cell (Volt)")
        st.line_chart(st.session_state.history["tx_voltage"], color="#ff4b4b")

# STREAMLIT_CHUNK:Bygger mottagarsidan
with col_rx:
    st.header("Mottagare (Fotodetektor)")
    
    if st.session_state.history:
        data = st.session_state.history
        
        st.subheader("Slutgiltigt mottaget meddelande")
        if data["original_text"] == data["rx_text"]:
            st.success(f"**{data['rx_text']}**")
        else:
            st.error(f"**{data['rx_text']}**")
            
        st.subheader("Mottagna Bitar (ren data)")
        st.code(data["rx_bits"] if data["rx_bits"] else "Inga bitar")
        
        st.subheader("Mottagen Utsignal (Idealiserad Volt)")
        if data["rx_voltage"]:
            st.line_chart(data["rx_voltage"], color="#21c354")
        else:
            st.info("Ingen graf att visa.")
            
        # Formatera datan för den beställda .txt-loggen
        # STREAMLIT_CHUNK:Skapar nedladdningsbar loggfil
        txt_log_content = (
            "--- LOGG FÖR OPTISK KOMMUNIKATION ---\n\n"
            f"Skickat meddelande: {data['original_text']}\n"
            f"Skickat meddelande konverterat till bits (inkl seq): {data['tx_bits']}\n"
            f"Mottagna bits (exkl seq): {data['rx_bits']}\n"
            f"Avkodat mottaget meddelande: {data['rx_text']}\n"
        )
        
        st.download_button(
            label="📄 Spara meddelandelogg (TXT)",
            data=txt_log_content.encode('utf-8'),
            file_name="meddelandelogg.txt",
            mime="text/plain",
            use_container_width=True
        )
    else:
        st.info("Väntar på inkommande signal...")