import streamlit as st
import numpy as np
import pandas as pd
import threading
import time
import datetime as dt

# ==============================================================================
# IMPORTERA BACKEND-FUNKTIONER
# ==============================================================================
from skicka import send_binary_list, lagg_till_avslutningsbit, Start_seq, Slut_seq
from taemot import receive_continuous, SynkadBitavkodare, berakna_sampel_per_bit
from Signalbehandling import huffman_encode, huffman_decode, tree, codes, encode, decode, hamming_correct
from grafer import minmax_utglesning, signalgraf, bitstrang
from filvisning import (bitar_till_bytes, dela_fildata, delvis_avkodning, visa_fil,
                        jamfor_filer, visa_kvitto, skillnadsbild, forstora)

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

with st.expander("Debug-meny"):
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
    sim_bitfel_procent = st.number_input(
        "Simulerade bitfel (%)", min_value=0.0, max_value=20.0, value=0.0, step=0.5,
        help="Endast i simuleringsläge. Vänder slumpvis bitar i nyttodatan för att visa hur Hamming-koden rättar fel."
    )

    st.markdown("### Manuell Bitsändning")
    manual_bits = st.text_input("Skriv in egna bitar (0 och 1):", placeholder="T.ex. 10101100")
    manual_send = st.button("Skicka Manuella Bitar", use_container_width=True)

step_time_input = st.session_state.step_time_val

col_tx, col_rx = st.columns(2)

# Mottagarens rubrik och en yta för livevisning skapas här, så att data
# kan visas i mottagarkolumnen medan sändningen pågår
with col_rx:
    st.header("Mottagare")
    rx_live = st.empty()

def rita_live_mottagning(avkodare, H, antal_bitar, senast_ritat):
    """
    Visar det som hittills tagits emot i mottagarkolumnen.
    Ritar bara om när nya bitar har kommit. Returnerar vad som ritades.
    """
    bitar = list(avkodare.payload)
    if not avkodare.klar:
        # De sista bitarna kan vara början på slutsekvensen och visas inte än
        bitar = bitar[:max(0, len(bitar) - len(Slut_seq))]
    lage = (avkodare.start_index is not None, len(bitar))
    if lage == senast_ritat:
        return senast_ritat

    with rx_live.container():
        st.subheader("Tar emot...")
        if avkodare.start_index is None:
            st.info("Väntar på startsekvens...")
            return lage
        st.progress(min(1.0, len(bitar) / max(1, antal_bitar)), text=f"{len(bitar)} av {antal_bitar} bitar mottagna")
        if H is None:  # Manuell bitsändning avkodas inte
            return lage
        try:
            delvis = delvis_avkodning(bitar, H)
        except Exception:
            return lage
        if delvis is None:
            pass
        elif delvis["typ"] == "text":
            st.text(delvis["text"] + "▌")
        elif delvis["filnamn"] is None:
            st.caption("Tar emot filnamn...")
        else:
            st.caption(f"{delvis['filnamn']}: {len(delvis['data'])} bytes hittills")
            visa_fil(delvis["filnamn"], delvis["data"], ofullstandig=True)
    return lage

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
    tx_filename = None
    file_content = None

    if normal_send:
        if uploaded_file is not None:
            # --- FILÖVERFÖRING ---
            trigger_send = True
            is_file_transfer = True
            original_text = f"[Skickad fil: {uploaded_file.name}]"
            
            try:
                # Läs in metadata och data till bytes
                tx_filename = uploaded_file.name
                filename_bytes = tx_filename.encode('utf-8')
                file_content = uploaded_file.getvalue()
                
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
                tx_bits_full = Start_seq + lagg_till_avslutningsbit(nyttobitar) + Slut_seq
                
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
            mottagar_fel = []
            sandar_fel = []
            redo = threading.Event()
            stopp = threading.Event()
            timeout_s = max(15.0, (len(tx_bits_full)) * step_time_input + 6.0)
            decode_threshold = st.session_state.threshold_val 
            
            # Avkodaren delas mellan mottagartråden och gränssnittet, så att
            # gränssnittet kan visa datan medan den tas emot
            sampel_per_bit = berakna_sampel_per_bit(step_time_input)
            sample_rate = sampel_per_bit / step_time_input
            avkodare = SynkadBitavkodare(sampel_per_bit, threshold=decode_threshold)
            sandar_trad = None
            
            if simulate_hw:
                # --- SIMULERINGSLÄGE ---
                # Simulerade bitfel läggs bara i nyttodatan, så att start- och slutsekvensen hittas
                sim_bits = np.array(tx_bits_full)
                if sim_bitfel_procent > 0:
                    nytta = slice(len(Start_seq), len(tx_bits_full) - len(Slut_seq))
                    sim_bits[nytta] ^= np.random.random(len(sim_bits[nytta])) < sim_bitfel_procent / 100
                
                idle_samples = int(4 * sampel_per_bit)
                sim_raw_data = np.concatenate([
                    np.full(idle_samples, 0.2),
                    np.repeat(np.where(sim_bits == 1, 4.8, 0.2), sampel_per_bit),
                    np.full(idle_samples, 0.2),
                ])
                sim_raw_data = (sim_raw_data + np.random.normal(0, 0.1, len(sim_raw_data))).tolist()
                
                def simulerad_mottagare():
                    # Matar avkodaren i samma takt som DAQ-kortet skulle ha gjort
                    try:
                        block = max(sampel_per_bit, int(sample_rate * 0.02))
                        t0 = time.monotonic()
                        for i in range(0, len(sim_raw_data), block):
                            if stopp.is_set() or avkodare.klar:
                                break
                            vanta = t0 + (i + block) / sample_rate - time.monotonic()
                            if vanta > 0:
                                time.sleep(vanta)
                            avkodare.mata_in(sim_raw_data[i:i + block])
                        motagna_resultat.append((avkodare.payload if avkodare.klar else [], avkodare.data))
                    except Exception as e:
                        mottagar_fel.append(str(e))
                
                mottagar_trad = threading.Thread(target=simulerad_mottagare, daemon=True)
                mottagar_trad.start()
                
            else:
                # --- RIKTIG HÅRDVARA (MOTTAGARE OCH SÄNDARE I EGNA TRÅDAR) ---
                def lyssna_i_bakgrunden():
                    try:
                        res_bits, res_raw = receive_continuous(
                            step_time=step_time_input, channel="Dev1/ai0", 
                            threshold=decode_threshold, timeout_s=timeout_s, ready_event=redo,
                            avkodare=avkodare, stop_event=stopp
                        )
                        motagna_resultat.append((res_bits, res_raw))
                    except Exception as e:
                        mottagar_fel.append(str(e))
                        redo.set()
                
                def sand_i_bakgrunden():
                    try:
                        send_binary_list(tx_bits_full, step_time=step_time_input, extra_time=extra_time_input,
                                         channel="Dev1/ao0", stop_event=stopp)
                    except Exception as e:
                        sandar_fel.append(str(e))
                        stopp.set()  # Ingen idé att fortsätta lyssna

                mottagar_trad = threading.Thread(target=lyssna_i_bakgrunden, daemon=True)
                mottagar_trad.start()
                
                if not redo.wait(timeout=5.0):
                    st.error("Mottagaren kunde inte startas inom 5 sekunder (Timeout).")
                    stopp.set()
                    mottagar_trad.join(timeout=5.0)
                    st.stop()
                    
                if mottagar_fel:
                    error_msg = mottagar_fel[0]
                    st.error(f"Kritiskt fel i mottagaren: {error_msg}")
                    if "nidaqmx" in error_msg.lower():
                        st.info("💡 Tips: Modulen 'nidaqmx' saknas. Slå på 'Simulera Hårdvara' i Debug-menyn om du vill testköra utan DAQ-kortet!")
                    st.stop()
                
                time.sleep(0.2) 
                
                sandar_trad = threading.Thread(target=sand_i_bakgrunden, daemon=True)
                sandar_trad.start()
            
            # --- LIVEVISNING MEDAN ÖVERFÖRINGEN PÅGÅR ---
            tx_status = st.empty()
            t_start = time.monotonic()
            forvantad_tid = max(len(tx_bits_full) * step_time_input, 1e-6)
            senast_ritat = None
            tradar = [t for t in (mottagar_trad, sandar_trad) if t is not None]
            try:
                while any(t.is_alive() for t in tradar):
                    andel = min(1.0, (time.monotonic() - t_start) / forvantad_tid)
                    tx_status.progress(andel, text=f"Sänder... {int(andel * len(tx_bits_full))} av {len(tx_bits_full)} bitar")
                    senast_ritat = rita_live_mottagning(avkodare, H, len(nyttobitar), senast_ritat)
                    time.sleep(0.3)
            finally:
                # Laddas gränssnittet om mitt i en överföring avbryts trådarna,
                # så att DAQ-kortet släpps och utgången nollställs
                if any(t.is_alive() for t in tradar):
                    stopp.set()
                    for t in tradar:
                        t.join(timeout=5.0)
            tx_status.empty()
            rx_live.empty()
            
            if sandar_fel:
                error_msg = sandar_fel[0]
                st.error(f"DAQ-fel (Sändare): {error_msg}")
                if "nidaqmx" in error_msg.lower():
                    st.info("💡 Tips: Modulen 'nidaqmx' saknas. Slå på 'Simulera Hårdvara' i Debug-menyn om du vill testköra utan DAQ-kortet!")
                st.stop()
            
            if mottagar_fel:
                st.error(f"Fel under mottagning: {mottagar_fel[0]}")
                st.stop()
            
            if simulate_hw:
                st.toast("Simulerad överföring och avkodning klar!", icon="✅")
            
            # Packa upp resultatet
            if motagna_resultat:
                rx_bits, raw_data = motagna_resultat[0]
            else:
                rx_bits, raw_data = [], []
            
            # Ta bort avslutningsbiten efter nyttodatan (se skicka.lagg_till_avslutningsbit)
            if not is_manual:
                rx_bits = rx_bits[:-1]
                
            # Avkoda mottagen signal
            rx_is_file = False
            rx_filename = None
            rx_file_data = None
            antal_rattade = None
            
            if rx_bits:
                if is_manual:
                    rx_text = "[Manuell sändning - Avkodning inaktiverad]"
                else:
                    try:
                        if len(rx_bits) != len(nyttobitar):
                            st.warning("Fel antal nyttobitar. Kontrollera synkronisering.")
                            
                        # Hamming-avkodning. Vid fel längd avkodas de hela block som finns,
                        # så att det som kom fram ändå kan visas och jämföras
                        n_hela = len(rx_bits) // 7 * 7
                        avkodade_bitar, antal_rattade = decode(
                            rx_bits[:n_hela], H, padding if n_hela == len(nyttobitar) else 0,
                            return_corrections=True
                        )
                        
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
                                
                                # Hitta gränsen mellan filnamn (text) och data (0x00)
                                rx_filename, rx_file_data = dela_fildata(bitar_till_bytes(payload_bits))
                                if rx_filename is not None:
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
            
            # Var i överföringen uppstod bitfel? De mottagna bitarna jämförs med de skickade
            # (båda finns i samma program). Tiderna används för att markera felen i grafen.
            fel_markeringar = None
            if rx_bits and not is_manual and H is not None and avkodare.start_index is not None:
                m = min(len(rx_bits), len(nyttobitar)) // 7 * 7
                if m > 0:
                    rattade_kodord, _ = hamming_correct(rx_bits[:m], H)
                    skickat = np.asarray(nyttobitar[:m])
                    kanalfel = np.asarray(rx_bits[:m]) != skickat
                    fel_kvar = rattade_kodord.ravel() != skickat
                    
                    def bit_till_tid(bitindex):
                        # Mitten av nyttobiten i mottagarens tidsaxel
                        sampel = avkodare.start_index + (len(Start_seq) + bitindex + 0.5) * sampel_per_bit
                        return (sampel / sample_rate).tolist()
                    
                    fel_markeringar = {
                        "rattade": bit_till_tid(np.nonzero(kanalfel & ~fel_kvar)[0]),
                        "kvar": bit_till_tid(np.nonzero(fel_kvar)[0]),
                    }
            
            # Rådata från fotodetektorn sparas för CSV-export vid både text och fil
            if raw_data:
                rx_times = np.arange(len(raw_data)) / sample_rate
                rx_df = pd.DataFrame({
                    "Tid (s)": rx_times, 
                    "Signal (V)": raw_data,
                    "Tröskelvärde (V)": [decode_threshold] * len(raw_data)
                }).set_index("Tid (s)")
            else:
                rx_df = None
            
            # Styrsignalen till LC-cellen (utglesas innan den visas)
            if tx_bits_full:
                tx_times = []
                tx_volts = []
                for i, val in enumerate(tx_bits_full):
                    t_start = i * step_time_input
                    t_end = (i + 1) * step_time_input - 1e-6
                    v = val * 5.0 * ((-1) ** i)
                    tx_times.extend([t_start, t_end])
                    tx_volts.extend([v, v])
                tx_df = pd.DataFrame({"Tid (s)": tx_times, "Spänning (V)": tx_volts}).set_index("Tid (s)")
            else:
                tx_df = None
            
            # Bygg logg och CSV en gång vid mottagning, så att tidsstämpel och
            # inställningar frysts från sändningstillfället och inte ändras vid omkörning
            # Jämför mottagen fil med den skickade (kvitto i gränssnittet)
            jamforelse = None
            if is_file_transfer and rx_file_data is not None:
                jamforelse = jamfor_filer(file_content, rx_file_data)
            
            if jamforelse is not None:
                avkodningsfel = not jamforelse["identisk"]
            elif is_file_transfer:
                avkodningsfel = True
            else:
                avkodningsfel = rx_text != original_text
            
            rx_tidpunkt = dt.datetime.now()
            txt_log_content = (
                "--- OPTKOM MESSAGE LOG ---\n\n"
                f"Timestamp: {rx_tidpunkt.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Sent message: {original_text}\n"
                f"Simulated hardware: {'Yes' if simulate_hw else 'No'}\n"
                f"Step time (s/bit): {step_time_input:.5f}\n"
                f"Sample rate (bit/s): {1.0 / step_time_input:.2f}\n"
                f"Extra time (s): {extra_time_input:.2f}\n"
                f"Threshold (V): {decode_threshold:.2f}\n"
                f"Sent message converted to bits (incl seq): {tx_bits_full}\n"
                f"Received bits (excl seq): {rx_bits}\n"
                f"Decoded received message: {rx_text}\n"
                f"Hamming corrections: {antal_rattade if antal_rattade is not None else '-'}\n"
                f"Bit errors left after Hamming: {len(fel_markeringar['kvar']) if fel_markeringar else '-'}\n"
                f"Error in decoding: {'Yes' if avkodningsfel else 'No'}\n"
            )
            if jamforelse is not None:
                txt_log_content += (
                    f"File size sent/received (bytes): {jamforelse['skickade_bytes']}/{jamforelse['mottagna_bytes']}\n"
                    f"Differing bytes: {jamforelse['fel_bytes']}\n"
                    f"SHA-256 sent: {jamforelse['sha_skickad']}\n"
                    f"SHA-256 received: {jamforelse['sha_mottagen']}\n"
                )
            csv_bytes = rx_df.to_csv(index=True).encode('utf-8') if rx_df is not None else None
            
            # Graferna får bara en utglesad version av signalen (min/max per segment).
            # Hela rådatan finns kvar i CSV-filen ovan.
            if rx_df is not None:
                rx_graf_df = minmax_utglesning(rx_df[["Signal (V)"]], "Signal (V)")
            else:
                rx_graf_df = None
            tx_graf_df = minmax_utglesning(tx_df, "Spänning (V)") if tx_df is not None else None

            # Spara state
            st.session_state.history = {
                "timestamp_str": rx_tidpunkt.strftime('%Y-%m-%d_%H-%M-%S'),
                "txt_log_bytes": txt_log_content.encode('utf-8'),
                "csv_bytes": csv_bytes,
                "original_text": original_text,
                "tx_bits": tx_bits_full,
                "tx_df": tx_graf_df,
                "rx_df": rx_graf_df,
                "rx_punkter_totalt": len(rx_df) if rx_df is not None else 0,
                "rx_bits": rx_bits,
                "rx_text": rx_text,
                "decode_threshold": decode_threshold,
                "rx_is_file": rx_is_file,
                "rx_filename": rx_filename,
                "rx_file_data": rx_file_data,
                "is_file_transfer": is_file_transfer,
                "tx_filename": tx_filename,
                "tx_file_data": file_content,
                "jamforelse": jamforelse,
                "antal_rattade": antal_rattade,
                "fel_markeringar": fel_markeringar,
            }

    def visa_sandarsignal(data):
        st.subheader("Skickade Bitar (inkl. start/stopp)")
        st.code(bitstrang(data["tx_bits"]), language=None, wrap_lines=True)

        if data.get("tx_df") is not None:
            st.subheader("Styrsignal till LC-cell (V)")
            st.altair_chart(signalgraf(data["tx_df"], "Spänning (V)", "#ff4b4b"), use_container_width=True)

    if st.session_state.history:
        data = st.session_state.history
        if not data.get("is_file_transfer", False):
            visa_sandarsignal(data)
        else:
            if data.get("tx_file_data") is not None:
                st.subheader("Skickad fil")
                visa_fil(data["tx_filename"], data["tx_file_data"])
            # Vid filöverföring är signalen mest till för felsökning, så den ligger hopfälld
            with st.expander("📈 Signal och bitar"):
                visa_sandarsignal(data)

# ---------------------------------------------------------
# 4. MOTTAGARSIDAN
# ---------------------------------------------------------
def visa_mottagarsignal(data):
    st.subheader("Mottagna Bitar (ren data)")
    st.code(bitstrang(data["rx_bits"]) if data["rx_bits"] else "Inga bitar", language=None, wrap_lines=True)

    st.subheader("Fotodetektorns Utsignal")
    if data.get("rx_df") is None:
        st.info("Ingen graf att visa.")
        return
    markeringar = data.get("fel_markeringar")
    st.altair_chart(signalgraf(data["rx_df"], "Signal (V)", "#21c354",
                               troskel=data.get("decode_threshold"), markeringar=markeringar),
                    use_container_width=True)

    totalt = data.get("rx_punkter_totalt", len(data["rx_df"]))
    text = "Streckad orange linje: tröskelvärde."
    if markeringar:
        text += (f" Blå streck: bitfel som Hamming-koden rättade ({len(markeringar['rattade'])} st)."
                 f" Röda streck: bitfel som finns kvar ({len(markeringar['kvar'])} st)."
                 " Felen hittas genom att jämföra med de skickade bitarna.")
    if totalt > len(data["rx_df"]):
        text += f" Visar {len(data['rx_df'])} av {totalt} sampel (min/max per segment)."
    st.caption(text + " All rådata finns i CSV-filen.")

with col_rx:
    if st.session_state.history:
        data = st.session_state.history
        
        st.subheader("Slutgiltigt mottaget meddelande/fil")
        if data.get("rx_is_file") and data.get("rx_file_data") is not None:
            if data.get("jamforelse") is not None:
                visa_kvitto(data["jamforelse"], data.get("antal_rattade"))
            else:
                st.success(f"**{data['rx_text']}**")
            
            visa_fil(data["rx_filename"], data["rx_file_data"])
            
            # Markera pixlar som skiljer sig från originalet
            if data.get("jamforelse") is not None and not data["jamforelse"]["identisk"]:
                diffbild, antal_fel_pixlar, lanat_huvud, antal_palettpixlar = skillnadsbild(
                    data["tx_file_data"], data["rx_file_data"]
                )
                if diffbild is not None:
                    bildtext = f"Rött: pixel med fel färg ({antal_fel_pixlar} st)"
                    if antal_palettpixlar:
                        bildtext += (f". Gult: rätt färg men nyansen ändrad av ett fel i bildens palett "
                                     f"({antal_palettpixlar} st)")
                    if lanat_huvud:
                        bildtext += ". Bildhuvudet skadades, så originalets huvud används för att visa pixlarna."
                    st.image(forstora(diffbild), caption=bildtext)
            
            st.download_button(
                label=f"Ladda ner mottagen fil ({data['rx_filename']})",
                data=data["rx_file_data"],
                file_name=data["rx_filename"],
                use_container_width=True
            )
        elif data["original_text"] == data["rx_text"]:
            st.success(f"**{data['rx_text']}**")
        else:
            st.error(f"**{data['rx_text']}**")
        
        if not data.get("rx_is_file") and data.get("antal_rattade") is not None:
            st.caption(f"Hamming-koden rättade {data['antal_rattade']} bitfel under överföringen.")
        
        if not data.get("is_file_transfer", False):
            visa_mottagarsignal(data)
        else:
            with st.expander("📈 Signal och bitar"):
                visa_mottagarsignal(data)

        st.markdown("### Nedladdning av data")
        col_btn1, col_btn2 = st.columns(2)

        with col_btn1:
            if data.get("txt_log_bytes") is not None:
                st.download_button(
                    label="📄 Spara textlogg (.txt)",
                    data=data["txt_log_bytes"],
                    file_name=f"sent-message-log-{data['timestamp_str']}.txt",
                    mime="text/plain",
                    use_container_width=True
                )

        with col_btn2:
            if data.get("csv_bytes") is not None:
                st.download_button(
                    label="📊 Spara analog rådata (.csv)",
                    data=data["csv_bytes"],
                    file_name=f"raw-data-{data['timestamp_str']}.csv",
                    mime="text/csv",
                    use_container_width=True
                )
    else:
        st.info("Väntar på inkommande signal...")