import streamlit as st
import numpy as np
import pandas as pd
import threading
import time
import datetime as dt

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

with st.expander("🛠️ Debug-meny"):
    st.markdown("### Justera Hårdvarutider")
    col_t1, col_t2 = st.columns(2)
    with col_t1:
        step_time_input = st.number_input("Step Time (s/bit)", min_value=0.001, max_value=1.0, value=0.10, step=0.001, format="%.3f")
        # Beräkna och visa överföringshastigheten i bit/s
        sample_rate = 1.0 / step_time_input if step_time_input > 0 else 0
        st.info(f"Överföringshastighet: **{sample_rate:.2f} bit/s**")
    with col_t2:
        extra_time_input = st.number_input("Extra Time (s)", min_value=0.0, max_value=5.0, value=1.0, step=0.1)
    
    st.markdown("### Simulering")
    simulate_hw = st.toggle("Simulera Hårdvara (Inget DAQ-kort)", value=False, help="Om aktiverad kommer ingen hårdvara att användas. Allt simuleras istället.")
    
    st.markdown("### Manuell Bitsändning")
    manual_bits = st.text_input("Skriv in egna bitar (0 och 1):", placeholder="T.ex. 10101100")
    manual_send = st.button("Skicka Manuella Bitar", use_container_width=True)

col_tx, col_rx = st.columns(2)

with col_tx:
    st.header("Sändare")
    
    # Dessa två rader saknades!
    message_input = st.text_input("Mata in meddelande att skicka:", placeholder="Skriv ditt ord här...")
    normal_send = st.button("Skicka Data", use_container_width=True)
    
    # Logik för att hantera båda sändningssätten
    trigger_send = False
    is_manual = False
    tx_bits_full = []
    original_text = ""
    
    if normal_send and message_input:
        trigger_send = True
        original_text = message_input.lower()
        
        # 1. Huffman-koda meddelandet via Signalbehandling.py
        try:
            encoded_bits = huffman_encode(original_text, codes)
            tx_bits_full = Start_seq + encoded_bits + Slut_seq
        except Exception as e:
            st.error(f"Kunde inte koda texten. Finns tecknen i tabellen? Fel: {e}")
            st.stop()
            
    elif manual_send and manual_bits:
        trigger_send = True
        is_manual = True
        original_text = "[Manuell bitsändning]"
        
        # Extrahera endast giltiga ettor och nollor
        clean_bits = [int(b) for b in manual_bits if b in ('0', '1')]
        if not clean_bits:
            st.error("Du måste ange giltiga bitar (endast 0 och 1).")
            st.stop()
            
        # Vi lägger till Start och Stopp sekvens så att mottagaren faktiskt triggas!
        tx_bits_full = Start_seq + clean_bits + Slut_seq
        
    elif normal_send and not message_input:
        st.warning("Vänligen mata in ett meddelande först.")

    if trigger_send:
        with st.spinner("Modulerar och sänder signal..."):
            # Beräkna styrsignalen (bipolär 5V alternerande) för grafen i gränssnittet
            tx_voltage = [val * 5.0 * ((-1) ** i) for i, val in enumerate(tx_bits_full)]
            
            # 2. Starta mottagaren i bakgrunden (Tråd) ELLER simulera
            motagna_resultat = []
            
            if simulate_hw:
                # --- SIMULERINGSLÄGE ---
                time.sleep(0.5) # Simulerad uppstart
                
                # Simulera fördröjningen för att skicka alla bitar i rätt hastighet
                simulated_transfer_time = len(tx_bits_full) * step_time_input
                time.sleep(simulated_transfer_time)
                
                # I verkligheten plockar receive_continuous bort start- och slutsekvensen, 
                # så vi gör samma sak i simuleringen.
                simulated_payload = tx_bits_full[len(Start_seq):-len(Slut_seq)]
                motagna_resultat.append(simulated_payload)
                
                st.toast("Simulerad överföring klar!", icon="🤖")
                
            else:
                # --- RIKTIG HÅRDVARA ---
                def lyssna_i_bakgrunden():
                    # Vi använder step_time_input från debug-menyn
                    res = receive_continuous(step_time=step_time_input, channel="Dev1/ai0", threshold=1.5)
                    motagna_resultat.append(res)

                mottagar_trad = threading.Thread(target=lyssna_i_bakgrunden)
                mottagar_trad.start()
                
                # 3. Ge DAQ-kortets mottagare tid att vakna
                time.sleep(0.5)
                
                # 4. Skicka signalen via hårdvaran med dynamisk step_time och extra_time
                try:
                    send_binary_list(tx_bits_full, step_time=step_time_input, extra_time=extra_time_input, channel="Dev1/ao0")
                except Exception as e:
                    st.error(f"DAQ-fel (Sändare): {e}")
                
                # 5. Invänta mottagaren
                mottagar_trad.join()
            
            # 6. Avkoda mottagen signal
            rx_bits = motagna_resultat[0] if motagna_resultat else []
            if rx_bits:
                if is_manual:
                    rx_text = "[Manuell sändning - Avkodning inaktiverad]"
                else:
                    try:
                        rx_text = huffman_decode(rx_bits, tree)
                    except Exception as e:
                        rx_text = f"Något blev fel vid avkodning: {e}"
            else:
                rx_text = "[Ingen data mottogs. Fick mottagaren ljus på sig?]"
            
            # Skapar en idealiserad representation för gränssnittets graf
            rx_voltage = [val * 5.0 for val in rx_bits]
            
            # Spara all insamlad data till session state
            st.session_state.history = {
                "original_text": original_text,
                "tx_bits": tx_bits_full,
                "tx_voltage": tx_voltage,
                "rx_voltage": rx_voltage,
                "rx_bits": rx_bits,
                "rx_text": rx_text
            }
    # STREAMLIT_CHUNK:Renderar grafer för sändare
    if st.session_state.history:
            st.subheader("Skickade Bitar (inkl. start/stopp)")
            st.code(st.session_state.history["tx_bits"])
            st.subheader("Styrsignal till LC-cell (Volt)")
            st.line_chart(st.session_state.history["tx_voltage"], color="#ff4b4b")

# STREAMLIT_CHUNK:Bygger mottagarsidan
with col_rx:
    st.header("Mottagare")
    
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
            label="📄 Spara meddelandelogg (.txt)",
            data=txt_log_content.encode('utf-8'),
            file_name=f"meddelandelogg-{dt.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt",
            mime="text/plain",
            use_container_width=True
        )
    else:
        st.info("Väntar på inkommande signal...")