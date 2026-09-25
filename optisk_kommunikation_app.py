import streamlit as st
import numpy as np
import pandas as pd
import threading
import time
import datetime as dt

# ==============================================================================
# IMPORTERA BACKEND-FUNKTIONER
# ==============================================================================
from skicka import send_binary_list, Start_seq, Slut_seq
from taemot import receive_continuous
from Signalbehandling import huffman_encode, huffman_decode, tree, codes

# ==============================================================================
# ANVÄNDARGRÄNSSNITT (STREAMLIT)
# ==============================================================================
st.set_page_config(page_title="Optisk Kommunikation", layout="wide")

# ---------------------------------------------------------
# 1. STATE & CALLBACKS FÖR SYNKRONISERAD INMATNING
# ---------------------------------------------------------
if 'history' not in st.session_state:
    st.session_state.history = None

# Initiera default-värden för tidsinställningar
if 'step_time_val' not in st.session_state:
    st.session_state.step_time_val = 0.100

if 'sample_rate_val' not in st.session_state:
    st.session_state.sample_rate_val = 10.0

def update_sample_rate():
    """Beräknar hastighet (bit/s) när step_time ändras."""
    if st.session_state.step_time_val > 0:
        st.session_state.sample_rate_val = 1.0 / st.session_state.step_time_val
    else:
        st.session_state.sample_rate_val = 0.0

def update_step_time():
    """Beräknar step_time (s/bit) när hastigheten ändras."""
    if st.session_state.sample_rate_val > 0:
        st.session_state.step_time_val = 1.0 / st.session_state.sample_rate_val
    else:
        st.session_state.step_time_val = 0.0

# ---------------------------------------------------------
# 2. HUVUDMENY
# ---------------------------------------------------------
st.title("Optisk Kommunikation - Kontrollpanel")
st.markdown("---")

with st.expander("🛠️ Debug-meny"):
    st.markdown("### Justera Hårdvarutider")
    col_t1, col_t2 = st.columns(2)
    
    with col_t1:
        # Båda dessa fält är nu direkt kopplade till varandra!
        st.number_input(
            "Step Time (s/bit)", 
            min_value=0.000001, max_value=2.0, step=0.001, format="%.5f", 
            key="step_time_val", 
            on_change=update_sample_rate
        )
        st.number_input(
            "Överföringshastighet (bit/s)", 
            min_value=0.5, max_value=1000.0, step=1.0, format="%.2f", 
            key="sample_rate_val", 
            on_change=update_step_time
        )
        
    with col_t2:
        extra_time_input = st.number_input("Extra Time (s)", min_value=0.0, max_value=5.0, value=1.0, step=0.1)
    st.markdown("### Simulering")    
    simulate_hw = st.toggle("Simulera Hårdvara (Inget DAQ-kort)", value=False, help="Om aktiverad kommer ingen hårdvara att användas. Allt simuleras istället.")
    
    st.markdown("### Manuell Bitsändning")
    manual_bits = st.text_input("Skriv in egna bitar (0 och 1):", placeholder="T.ex. 10101100")
    manual_send = st.button("Skicka Manuella Bitar", use_container_width=True)

# Läs av den synkroniserade tiden till en lättanvänd lokal variabel
step_time_input = st.session_state.step_time_val

col_tx, col_rx = st.columns(2)

# ---------------------------------------------------------
# 3. SÄNDARSIDAN
# ---------------------------------------------------------
with col_tx:
    st.header("Sändare")
    
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
        
        # Huffman-koda meddelandet
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
            
        # Lägg till Start och Stopp sekvens så att mottagaren faktiskt triggas
        tx_bits_full = Start_seq + clean_bits + Slut_seq
        
    elif normal_send and not message_input:
        st.warning("Vänligen mata in ett meddelande först.")

    if trigger_send:
        with st.spinner("Modulerar och sänder signal..."):
            
            # 2. Starta mottagaren i bakgrunden (Tråd) ELLER simulera
            motagna_resultat = []
            
            if simulate_hw:
                # --- SIMULERINGSLÄGE ---
                time.sleep(0.5) # Simulerad uppstart
                
                # Simulera fördröjningen
                simulated_transfer_time = len(tx_bits_full) * step_time_input
                time.sleep(simulated_transfer_time)
                
                # Plocka bort start- och slutsekvensen
                simulated_payload = tx_bits_full[len(Start_seq):-len(Slut_seq)]
                motagna_resultat.append(simulated_payload)
                
                st.toast("Simulerad överföring klar!", icon="🤖")
                
            else:
                # --- RIKTIG HÅRDVARA ---
                def lyssna_i_bakgrunden():
                    res = receive_continuous(step_time=step_time_input, channel="Dev1/ai0", threshold=1.5)
                    motagna_resultat.append(res)

                mottagar_trad = threading.Thread(target=lyssna_i_bakgrunden)
                mottagar_trad.start()
                
                time.sleep(0.5) # Ge DAQ-mottagaren tid att vakna
                
                # Skicka signal via hårdvaran
                try:
                    send_binary_list(tx_bits_full, step_time=step_time_input, extra_time=extra_time_input, channel="Dev1/ao0")
                except Exception as e:
                    st.error(f"DAQ-fel (Sändare): {e}")
                
                mottagar_trad.join()
            
            # Avkoda mottagen signal
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
            
            # Skapa fyrkantsvåg för SÄNDAREN (Bipolär alternerande)
            tx_times = []
            tx_volts = []
            for i, val in enumerate(tx_bits_full):
                t_start = i * step_time_input
                t_end = (i + 1) * step_time_input - 1e-6
                v = val * 5.0 * ((-1) ** i)
                tx_times.extend([t_start, t_end])
                tx_volts.extend([v, v])
            
            tx_df = pd.DataFrame({"Tid (s)": tx_times, "Spänning (V)": tx_volts}).set_index("Tid (s)")
            
            # Skapa fyrkantsvåg för MOTTAGAREN (Unipolär 5V idealiserad)
            rx_times = []
            rx_volts = []
            for i, val in enumerate(rx_bits):
                t_start = i * step_time_input
                t_end = (i + 1) * step_time_input - 1e-6
                v = val * 5.0
                rx_times.extend([t_start, t_end])
                rx_volts.extend([v, v])
            
            rx_df = pd.DataFrame({"Tid (s)": rx_times, "Spänning (V)": rx_volts}).set_index("Tid (s)") if rx_bits else None
            
            # Spara insamlad data till session state
            st.session_state.history = {
                "original_text": original_text,
                "tx_bits": tx_bits_full,
                "tx_df": tx_df,
                "rx_df": rx_df,
                "rx_bits": rx_bits,
                "rx_text": rx_text
            }

    # Renderar grafer för sändare
    if st.session_state.history:
        st.subheader("Skickade Bitar (inkl. start/stopp)")
        st.code(st.session_state.history["tx_bits"])
        st.subheader("Styrsignal till LC-cell (Volt)")
        st.line_chart(st.session_state.history["tx_df"], color="#ff4b4b")

# ---------------------------------------------------------
# 4. MOTTAGARSIDAN
# ---------------------------------------------------------
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
        if data["rx_df"] is not None:
            st.line_chart(data["rx_df"], color="#21c354")
        else:
            st.info("Ingen graf att visa.")
            
        # Formatera datan för .txt-loggen
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