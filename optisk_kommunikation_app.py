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

if 'threshold_val' not in st.session_state:
    st.session_state.threshold_val = 3.0

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

def update_threshold():
    if st.session_state.threshold_val < 0.0:
        st.session_state.threshold_val = 0.0
    if st.session_state.threshold_val > 5.0:
        st.session_state.threshold_val = 5.0

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
        st.number_input("Tröskelvärde (V)", min_value=0.0, max_value=5.0, step=0.1, key="threshold_val")
    
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
    uploaded_file = st.file_uploader("Eller välj en fil att skicka (rekommenderas max 1 KB)", type=None)
    
    normal_send = st.button("Skicka Data", use_container_width=True)
    
    trigger_send = False
    is_manual = False
    is_file_transfer = False
    tx_bits_full = []
    nyttobitar = []
    original_text = ""
    H = None
    padding = 0
    
    if normal_send:
        if uploaded_file is not None:
            # --- FILÖVERFÖRING ---
            trigger_send = True
            is_file_transfer = True
            original_text = f"[Skickad fil: {uploaded_file.name}]"
            
            try:
                # Läs in metadata och data till bytes
                filename_bytes = uploaded_file.name.encode('utf-8')
                file_content = uploaded_file.read()
                
                # Format: filnamn + Null-byte (0x00) + filinnehåll
                payload_bytes = filename_bytes + b'\x00' + file_content
                
                # Konvertera bytes till lista med 1:or och 0:or
                raw_bits = []
                for b in payload_bytes:
                    for i in range(7, -1, -1):
                        raw_bits.append((b >> i) & 1)
                
                # Lägg till FLAGGA [1] för att indikera fil!
                data_to_encode = [1] + raw_bits
                
            except Exception as e:
                st.error(f"Kunde inte läsa in filen. Fel: {e}")
                st.stop()
                
        elif message_input:
            # --- TEXTÖVERFÖRING ---
            trigger_send = True
            original_text = message_input.lower()
            
            try:
                # 1. Huffman-kodning för text
                komprimerade_bitar = huffman_encode(original_text, codes)
                
                # Lägg till FLAGGA [0] för att indikera text!
                data_to_encode = [0] + komprimerade_bitar
            except Exception as e:
                st.error(f"Kunde inte koda texten. Fel: {e}")
                st.stop()
        else:
            st.warning("Vänligen mata in ett meddelande eller ladda upp en fil först.")

        if trigger_send:
            try:
                # Felkorrigering (Hamming) på antingen text- eller fildatan
                kodade_bitar, H, padding = encode(data_to_encode)
                nyttobitar = kodade_bitar.tolist()
                
                # Varning om slutsekvens finns i nyttodatan (Slump för filer)
                for i in range(len(nyttobitar) - len(Slut_seq) + 1):
                    if nyttobitar[i:i + len(Slut_seq)] == Slut_seq:
                        st.warning("Slutsekvensen råkar förekomma i nyttodatan! Ändra ditt meddelande eller modifiera/byt ut filen.")
                        st.stop()
                        
                tx_bits_full = Start_seq + nyttobitar + Slut_seq
                
            except Exception as e:
                st.error(f"Kunde inte applicera felkorrigering (Hamming). Fel: {e}")
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

    if trigger_send:
        with st.spinner("Modulerar och sänder signal..."):
            
            motagna_resultat = []
            redo = threading.Event()
            timeout_s = max(15.0, (len(tx_bits_full)) * step_time_input + 6.0)
            decode_threshold = st.session_state.threshold_val 
            
            if simulate_hw:
                # --- SIMULERINGSLÄGE ---
                time.sleep(0.5) 
                simulated_transfer_time = len(tx_bits_full) * step_time_input
                time.sleep(simulated_transfer_time)
                
                sampel_per_bit = max(25, int(round(5000 * step_time_input)))
                
                idle_samples = int(4 * sampel_per_bit)
                sim_raw_data = [0.2] * idle_samples
                
                for bit in tx_bits_full:
                    volts = 4.8 if bit == 1 else 0.2
                    sim_raw_data.extend([volts] * sampel_per_bit)
                
                sim_raw_data.extend([0.2] * idle_samples)
                sim_raw_data = np.array(sim_raw_data) + np.random.normal(0, 0.1, len(sim_raw_data))
                
                # 2. AVKODA DEN SIMULERADE SIGNALEN
                from taemot import SynkadBitavkodare
                sim_avkodare = SynkadBitavkodare(sampel_per_bit, threshold=decode_threshold)
                
                sim_avkodare.mata_in(sim_raw_data.tolist())
                
                simulated_payload = sim_avkodare.payload if sim_avkodare.klar else []
                motagna_resultat.append((simulated_payload, sim_raw_data.tolist()))
                st.toast("Simulerad överföring och avkodning klar!", icon="🤖")
                
            else:
                # --- RIKTIG HÅRDVARA (MAIN-TRÅDNING) ---
                mottagar_fel = [] 
                
                def lyssna_i_bakgrunden():
                    try:
                        res_bits, res_raw = receive_continuous(
                            step_time=step_time_input, channel="Dev1/ai0", 
                            threshold=decode_threshold, timeout_s=timeout_s, ready_event=redo
                        )
                        motagna_resultat.append((res_bits, res_raw))
                    except Exception as e:
                        mottagar_fel.append(str(e))
                        redo.set()

                mottagar_trad = threading.Thread(target=lyssna_i_bakgrunden)
                mottagar_trad.start()
                
                if not redo.wait(timeout=5.0):
                    st.error("Mottagaren kunde inte startas inom 5 sekunder (Timeout).")
                    mottagar_trad.join(timeout=timeout_s + 3)
                    st.stop()
                    
                if mottagar_fel:
                    error_msg = mottagar_fel[0]
                    st.error(f"Kritiskt fel i mottagaren: {error_msg}")
                    if "nidaqmx" in error_msg.lower():
                        st.info("💡 Tips: Modulen 'nidaqmx' saknas. Slå på 'Simulera Hårdvara' i Debug-menyn om du vill testköra utan DAQ-kortet!")
                    st.stop()
                
                time.sleep(0.2) 
                
                try:
                    send_binary_list(tx_bits_full, step_time=step_time_input, extra_time=extra_time_input, channel="Dev1/ao0")
                except Exception as e:
                    error_msg = str(e)
                    st.error(f"DAQ-fel (Sändare): {error_msg}")
                    if "nidaqmx" in error_msg.lower():
                        st.info("💡 Tips: Modulen 'nidaqmx' saknas. Slå på 'Simulera Hårdvara' i Debug-menyn om du vill testköra utan DAQ-kortet!")
                    st.stop()
                
                mottagar_trad.join(timeout=timeout_s + 3)
                
                if mottagar_fel:
                    st.error(f"Fel under mottagning: {mottagar_fel[0]}")
                    st.stop()
            
            # Packa upp resultatet
            if motagna_resultat:
                rx_bits, raw_data = motagna_resultat[0]
            else:
                rx_bits, raw_data = [], []
                
            # Avkoda mottagen signal
            rx_is_file = False
            rx_filename = None
            rx_file_data = None
            
            if rx_bits:
                if is_manual:
                    rx_text = "[Manuell sändning - Avkodning inaktiverad]"
                else:
                    try:
                        if len(rx_bits) != len(nyttobitar):
                            st.warning("Fel antal nyttobitar. Kontrollera synkronisering.")
                            
                        # Hamming-avkodning
                        avkodade_bitar = decode(rx_bits, H, padding)
                        
                        if len(avkodade_bitar) > 0:
                            # 1. Separera Flagga och Data
                            flag = avkodade_bitar[0]
                            payload_bits = avkodade_bitar[1:]
                            
                            if flag == 0:
                                # Data är Text
                                rx_text = huffman_decode(payload_bits, tree)
                                
                            elif flag == 1:
                                # Data är en Fil! Konvertera bits till bytes
                                rx_is_file = True
                                byte_array = bytearray()
                                for i in range(0, len(payload_bits), 8):
                                    byte_chunk = payload_bits[i:i+8]
                                    if len(byte_chunk) == 8:
                                        b_val = 0
                                        for bit in byte_chunk:
                                            b_val = (b_val << 1) | bit
                                        byte_array.append(b_val)
                                
                                rx_bytes = bytes(byte_array)
                                
                                # Hitta gränsen mellan filnamn (text) och data (0x00)
                                separator_idx = rx_bytes.find(b'\x00')
                                if separator_idx != -1:
                                    rx_filename = rx_bytes[:separator_idx].decode('utf-8', errors='ignore')
                                    rx_file_data = rx_bytes[separator_idx+1:]
                                    rx_text = f"Mottagen fil: {rx_filename} ({len(rx_file_data)} bytes)"
                                else:
                                    rx_text = "[Fel vid läsning: Hittade ingen metadata/filnamn i den avkodade datan]"
                            else:
                                rx_text = "[Mottog okänd datatyp (Flagga är varken 0 eller 1)]"
                        else:
                            rx_text = "[Tomt meddelande mottaget efter avkodning]"

                    except Exception as e:
                        rx_text = f"Något blev fel vid avkodning: {e}"
            else:
                rx_text = "[Ingen data mottogs]"
            
            # Skapa grafer ENDAST om det inte är en filöverföring (för att undvika lagg)
            if not is_file_transfer:
                tx_times = []
                tx_volts = []
                for i, val in enumerate(tx_bits_full):
                    t_start = i * step_time_input
                    t_end = (i + 1) * step_time_input - 1e-6
                    v = val * 5.0 * ((-1) ** i)
                    tx_times.extend([t_start, t_end])
                    tx_volts.extend([v, v])
                tx_df = pd.DataFrame({"Tid (s)": tx_times, "Spänning (V)": tx_volts}).set_index("Tid (s)")
                
                if raw_data:
                    sampel_per_bit = max(25, int(round(5000 * step_time_input)))
                    sample_rate = sampel_per_bit / step_time_input
                    rx_times = np.arange(len(raw_data)) / sample_rate
                    rx_df = pd.DataFrame({
                        "Tid (s)": rx_times, 
                        "Signal (V)": raw_data,
                        "Tröskelvärde (V)": [decode_threshold] * len(raw_data)
                    }).set_index("Tid (s)")
                else:
                    rx_df = None
            else:
                tx_df = None
                rx_df = None
            
            # Spara state
            st.session_state.history = {
                "original_text": original_text,
                "tx_bits": tx_bits_full,
                "tx_df": tx_df,
                "rx_df": rx_df,
                "rx_bits": rx_bits,
                "rx_text": rx_text,
                "decode_threshold": decode_threshold,
                "rx_is_file": rx_is_file,
                "rx_filename": rx_filename,
                "rx_file_data": rx_file_data,
                "is_file_transfer": is_file_transfer
            }

    if st.session_state.history:
        st.subheader("Skickade Bitar (inkl. start/stopp)")
        st.code(st.session_state.history["tx_bits"])
        
        if not st.session_state.history.get("is_file_transfer", False):
            st.subheader("Styrsignal till LC-cell (V)")
            st.line_chart(st.session_state.history["tx_df"], color="#ff4b4b")
        else:
            st.info("ℹ️ Grafer är inaktiverade vid filöverföring för att undvika lagg.")

# ---------------------------------------------------------
# 4. MOTTAGARSIDAN
# ---------------------------------------------------------
with col_rx:
    st.header("Mottagare")
    
    if st.session_state.history:
        data = st.session_state.history
        
        st.subheader("Slutgiltigt mottaget meddelande/fil")
        if data.get("rx_is_file") and data.get("rx_file_data"):
            st.success(f"**{data['rx_text']}**")
            st.download_button(
                label=f"💾 Ladda ner mottagen fil ({data['rx_filename']})",
                data=data["rx_file_data"],
                file_name=data["rx_filename"],
                use_container_width=True
            )
        elif data["original_text"] == data["rx_text"]:
            st.success(f"**{data['rx_text']}**")
        else:
            st.error(f"**{data['rx_text']}**")
        
        st.subheader("Fotodetektorns Utsignal")
        if not data.get("is_file_transfer", False):
            if data["rx_df"] is not None:
                st.line_chart(data["rx_df"], color=["#21c354", "#ffaa00"])
            else:
                st.info("Ingen graf att visa.")

            st.subheader("Mottagna Bitar (ren data)")
            st.code(data["rx_bits"] if data["rx_bits"] else "Inga bitar")

        else:
            st.info("ℹ️ Grafer och rådata är inaktiverade vid filöverföring för att undvika lagg.")
            
        txt_log_content = (
            "--- OPTKOM MESSAGE LOG ---\n\n"
            f"Timestamp: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Sent message: {data['original_text']}\n"
            f"Simulated hardware: {'Yes' if simulate_hw else 'No'}\n"
            f"Step time (s/bit): {step_time_input:.5f}\n"
            f"Sample rate (bit/s): {1.0 / step_time_input:.2f}\n"
            f"Extra time (s): {extra_time_input:.2f}\n"
            f"Threshold (V): {data.get('decode_threshold', st.session_state.threshold_val):.2f}\n"
            f"Sent message converted to bits (incl seq): {data['tx_bits']}\n"
            f"Received bits (excl seq): {data['rx_bits']}\n"
            f"Decoded received message: {data['rx_text']}\n"
            f"Error in decoding: {'Yes' if data['rx_text'] != data['original_text'] else 'No'}\n"
        )
        
        st.markdown("### Nedladdning av data")
        col_btn1, col_btn2 = st.columns(2)
        
        with col_btn1:
            st.download_button(
                label="📄 Spara textlogg (.txt)",
                data=txt_log_content.encode('utf-8'),
                file_name=f"sent-message-log-{dt.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.txt",
                mime="text/plain",
                use_container_width=True
            )
            
        with col_btn2:
            if data["rx_df"] is not None:
                csv_data = data["rx_df"].to_csv(index=True).encode('utf-8')
                st.download_button(
                    label="📊 Spara analog rådata (.csv)",
                    data=csv_data,
                    file_name=f"raw-data-{dt.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.csv",
                    mime="text/csv",
                    use_container_width=True
                )
    else:
        st.info("Väntar på inkommande signal...")