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
from Signalbehandling import huffman_encode, huffman_decode, tree, codes, encode, decode

# ==============================================================================
# ANVÄNDARGRÄNSSNITT (STREAMLIT)
# ==============================================================================
st.set_page_config(page_title="Optisk Kommunikation", layout="wide")

# ---------------------------------------------------------
# 1. STATE & CALLBACKS FÖR SYNKRONISERAD INMATNING
# ---------------------------------------------------------
if 'history' not in st.session_state:
    st.session_state.history = None

if 'step_time_val' not in st.session_state:
    st.session_state.step_time_val = 0.004

if 'sample_rate_val' not in st.session_state:
    st.session_state.sample_rate_val = 250.0

def update_sample_rate():
    if st.session_state.step_time_val > 0:
        st.session_state.sample_rate_val = 1.0 / st.session_state.step_time_val
    else:
        st.session_state.sample_rate_val = 0.0

def update_step_time():
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
    simulate_hw = st.toggle("Simulera Hårdvara (Inget DAQ-kort)", value=False)
    
    st.markdown("### Manuell Bitsändning")
    manual_bits = st.text_input("Skriv in egna bitar (0 och 1):", placeholder="T.ex. 10101100")
    manual_send = st.button("Skicka Manuella Bitar", use_container_width=True)

step_time_input = st.session_state.step_time_val

col_tx, col_rx = st.columns(2)

# ---------------------------------------------------------
# 3. SÄNDARSIDAN
# ---------------------------------------------------------
with col_tx:
    st.header("Sändare")
    
    message_input = st.text_input("Mata in meddelande att skicka:", placeholder="Skriv ditt ord här...")
    normal_send = st.button("Skicka Data", use_container_width=True)
    
    trigger_send = False
    is_manual = False
    tx_bits_full = []
    nyttobitar = []
    original_text = ""
    H = None
    padding = 0
    
    if normal_send and message_input:
        trigger_send = True
        original_text = message_input.lower()
        
        try:
            # 1. Huffman-kodning och felkorrigering
            komprimerade_bitar = huffman_encode(original_text, codes)
            kodade_bitar, H, padding = encode(komprimerade_bitar)
            nyttobitar = kodade_bitar.tolist()
            
            # 2. Varning om slutsekvens finns i nyttodatan (Från main)
            for i in range(len(nyttobitar) - len(Slut_seq) + 1):
                if nyttobitar[i:i + len(Slut_seq)] == Slut_seq:
                    st.warning("Slutsekvensen förekommer i nyttodatan. Välj annat meddelande.")
                    st.stop()
                    
            tx_bits_full = Start_seq + nyttobitar + Slut_seq
            
        except Exception as e:
            st.error(f"Kunde inte koda texten. Fel: {e}")
            st.stop()
            
    elif manual_send and manual_bits:
        trigger_send = True
        is_manual = True
        original_text = "[Manuell bitsändning]"
        
        clean_bits = [int(b) for b in manual_bits if b in ('0', '1')]
        if not clean_bits:
            st.error("Du måste ange giltiga bitar (endast 0 och 1).")
            st.stop()
            
        nyttobitar = clean_bits
        tx_bits_full = Start_seq + clean_bits + Slut_seq
        
    elif normal_send and not message_input:
        st.warning("Vänligen mata in ett meddelande först.")

    if trigger_send:
        with st.spinner("Modulerar och sänder signal..."):
            
            motagna_resultat = []
            redo = threading.Event()
            timeout_s = max(15.0, (len(tx_bits_full)) * step_time_input + 6.0)
            
            if simulate_hw:
                # --- SIMULERINGSLÄGE ---
                time.sleep(0.5) 
                simulated_transfer_time = len(tx_bits_full) * step_time_input
                time.sleep(simulated_transfer_time)
                
                simulated_payload = tx_bits_full[len(Start_seq):-len(Slut_seq)]
                motagna_resultat.append(simulated_payload)
                st.toast("Simulerad överföring klar!", icon="🤖")
                
            else:
                # --- RIKTIG HÅRDVARA (MAIN-TRÅDNING) ---
                def lyssna_i_bakgrunden():
                    res = receive_continuous(
                        step_time=step_time_input, channel="Dev1/ai0", 
                        threshold=2.3, timeout_s=timeout_s, ready_event=redo
                    )
                    motagna_resultat.append(res)

                mottagar_trad = threading.Thread(target=lyssna_i_bakgrunden)
                mottagar_trad.start()
                
                if not redo.wait(timeout=5.0):
                    st.error("Mottagaren kunde inte startas.")
                    mottagar_trad.join(timeout=timeout_s + 3)
                    st.stop()
                
                time.sleep(0.2) # Viloperiod före sändning
                
                try:
                    send_binary_list(tx_bits_full, step_time=step_time_input, extra_time=extra_time_input, channel="Dev1/ao0")
                except Exception as e:
                    st.error(f"DAQ-fel (Sändare): {e}")
                
                mottagar_trad.join(timeout=timeout_s + 3)
            
            # Avkoda mottagen signal
            rx_bits = motagna_resultat[0] if motagna_resultat else []
            if rx_bits:
                if is_manual:
                    rx_text = "[Manuell sändning - Avkodning inaktiverad]"
                else:
                    try:
                        if len(rx_bits) != len(nyttobitar):
                            st.warning("Fel antal nyttobitar. Kontrollera synkronisering.")
                            
                        avkodade_bitar = decode(rx_bits, H, padding)
                        rx_text = huffman_decode(avkodade_bitar, tree)
                    except Exception as e:
                        rx_text = f"Något blev fel vid avkodning: {e}"
            else:
                rx_text = "[Ingen data mottogs]"
            
            # Skapa graf Sändare
            tx_times = []
            tx_volts = []
            for i, val in enumerate(tx_bits_full):
                t_start = i * step_time_input
                t_end = (i + 1) * step_time_input - 1e-6
                v = val * 5.0 * ((-1) ** i)
                tx_times.extend([t_start, t_end])
                tx_volts.extend([v, v])
            tx_df = pd.DataFrame({"Tid (s)": tx_times, "Spänning (V)": tx_volts}).set_index("Tid (s)")
            
            # Skapa graf Mottagare
            rx_times = []
            rx_volts = []
            for i, val in enumerate(rx_bits):
                t_start = i * step_time_input
                t_end = (i + 1) * step_time_input - 1e-6
                v = val * 5.0
                rx_times.extend([t_start, t_end])
                rx_volts.extend([v, v])
            rx_df = pd.DataFrame({"Tid (s)": rx_times, "Spänning (V)": rx_volts}).set_index("Tid (s)") if rx_bits else None
            
            # Spara state
            st.session_state.history = {
                "original_text": original_text,
                "tx_bits": tx_bits_full,
                "tx_df": tx_df,
                "rx_df": rx_df,
                "rx_bits": rx_bits,
                "rx_text": rx_text
            }

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