import streamlit as st
import numpy as np
import pandas as pd
import threading
import time
import datetime as dt

# ==============================================================================
# IMPORTERA BACKEND-FUNKTIONER
# ==============================================================================
from skicka import (send_binary_list, bygg_ram, till_spanningar, Start_seq,
                    LANGDFALT_BITAR, LANGDFALT_KOPIOR, AO_SAMPEL_PER_BIT)
from taemot import receive_continuous, SynkadBitavkodare, samplingsparametrar
from Signalbehandling import huffman_encode, huffman_decode, tree, codes
from grafer import minmax_utglesning, signalgraf, bitstrang
from filvisning import (bitar_till_bytes, dela_fildata, delvis_avkodning, visa_fil,
                        jamfor_filer, visa_kvitto, skillnadsbild, forstora)
from kalibreringsdata import kor_kalibrering, las_parametrar

# ==============================================================================
# ANVÄNDARGRÄNSSNITT (STREAMLIT)
# ==============================================================================
st.set_page_config(page_title="Optisk Kommunikation", layout="wide")

# ---------------------------------------------------------
# 1. STATE & CALLBACKS FÖR SYNKRONISERAD INMATNING
# ---------------------------------------------------------
if 'history' not in st.session_state:
    st.session_state.history = None

def tillampa_kalibrering(step_time, threshold):
    """Sätter inställningarna till givna kalibrerade värden. parametrar.py ändras aldrig här."""
    st.session_state.step_time_val = step_time
    st.session_state.sample_rate_val = round(1.0 / step_time, 2)
    st.session_state.threshold_val = round(threshold, 2)

# Vid uppstart hämtas de senast kalibrerade värdena från parametrar.py.
# Ändras inställningarna efteråt påverkas inte parametrar.py.
if 'kalibrering' not in st.session_state:
    st.session_state.kalibrering = las_parametrar()
    tillampa_kalibrering(st.session_state.kalibrering["step_time"], st.session_state.kalibrering["threshold"])

# En nyss gjord kalibrering (eller "Återställ kalibrerade värden") tillämpas innan
# inställningsfälten skapas, eftersom Streamlit inte tillåter ändringar efter det
vantande_kalibrering = st.session_state.pop("tillampa_kalibrering", None)
if vantande_kalibrering is not None:
    tillampa_kalibrering(*vantande_kalibrering)

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

def visa_kalibreringsinfo(parametrar, senaste_korning):
    """Innehållet i knappen 'Senaste kalibrering'."""
    st.markdown(f"**Senast kalibrerad:** {parametrar['tid'] or 'okänt (inget datum i parametrar.py)'}")
    st.table(pd.DataFrame([
        ("Bitfrekvens", f"{1 / parametrar['step_time']:.0f} bit/s ({parametrar['step_time'] * 1000:.3f} ms/bit)"),
        ("Tröskel", f"{parametrar['threshold']:.3f} V"),
    ], columns=["", "Värde"]).set_index(""))
    if parametrar["kommentarer"]:
        st.caption(" ".join(parametrar["kommentarer"]))
    st.caption(f"Fil: {parametrar['fil']}")

    if senaste_korning is not None:
        st.markdown("**Svep i senaste körningen**" + (" (simulerad)" if senaste_korning["simulerad"] else ""))
        st.table(pd.DataFrame([
            (r["frekvens"], "OK" if r["giltiga"] else "FEL",
             f"{min(r['giltiga']):.2f}–{max(r['giltiga']):.2f} V" if r["giltiga"] else "-",
             f"{r['vilonivå']:.3f} V", f"{r['hogsta']:.3f} V")
            for r in senaste_korning["svep"]
        ], columns=["bit/s", "Resultat", "Godkända trösklar", "Vila", "Max"]).set_index("bit/s"))

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
        help="Endast i simuleringsläge. Vänder slumpvis bitar i nyttodatan för att visa hur bitfel påverkar meddelandet."
    )

    st.markdown("### Kalibrering")
    kalibrering = st.session_state.kalibrering
    status = (f"Kalibrerat {kalibrering['tid'] or '(okänt datum)'}: {1 / kalibrering['step_time']:.0f} bit/s, "
              f"tröskel {kalibrering['threshold']:.2f} V (parametrar.py)")
    if (abs(st.session_state.step_time_val - kalibrering["step_time"]) > 1e-9
            or abs(st.session_state.threshold_val - round(kalibrering["threshold"], 2)) > 1e-9):
        status += ". Inställningarna ovan avviker just nu från kalibreringen."
    st.caption(status)

    col_k1, col_k2, col_k3 = st.columns(3)
    with col_k1:
        kalibrera = st.button("Kalibrera nu", use_container_width=True,
                              help="Kör kalibrering.py: sveper 200–350 bit/s, gör ett långt test med 3000 bitar "
                                   "och väljer bitfrekvens och tröskel. Tar 1–2 minuter. Resultatet sparas i "
                                   "parametrar.py. Med 'Simulera Hårdvara' påslaget simuleras mätningarna "
                                   "och inget sparas.")
    with col_k2:
        if st.button("Återställ kalibrerade värden", use_container_width=True):
            st.session_state.kalibrering = las_parametrar()
            st.session_state.tillampa_kalibrering = (st.session_state.kalibrering["step_time"],
                                                     st.session_state.kalibrering["threshold"])
            st.rerun()
    with col_k3:
        with st.popover("Senaste kalibrering", use_container_width=True):
            visa_kalibreringsinfo(kalibrering, st.session_state.get("senaste_kalibreringskorning"))

    if kalibrera:
        framsteg = st.progress(0.0, text="Startar kalibreringen...")
        try:
            resultat = kor_kalibrering(simulate_hw, lambda text, andel: framsteg.progress(andel, text=text))
        except Exception as e:
            tips = (" Modulen 'nidaqmx' saknas. Slå på 'Simulera Hårdvara' för att testa utan DAQ-kortet."
                    if "nidaqmx" in str(e).lower() else "")
            st.session_state.kalibrering_meddelande = ("error", f"Kalibreringen avbröts: {e}.{tips}")
        else:
            st.session_state.senaste_kalibreringskorning = {"svep": resultat["svep"], "simulerad": simulate_hw}
            vald = resultat["vald"]
            if vald is None:
                st.session_state.kalibrering_meddelande = (
                    "warning", f"Kalibreringen misslyckades, så de tidigare värdena behålls. {resultat['meddelande']}")
            else:
                st.session_state.tillampa_kalibrering = (1 / vald["frekvens"], vald["threshold"])
                text = (f"{vald['frekvens']} bit/s och tröskel {vald['threshold']:.2f} V "
                        f"(mitt i {vald['troskel_min']:.2f}–{vald['troskel_max']:.2f} V)")
                if simulate_hw:
                    st.session_state.kalibrering_meddelande = (
                        "success", f"Simulerad kalibrering klar: {text}. Värdena används i den här sessionen "
                                   "men sparas inte i parametrar.py.")
                else:
                    st.session_state.kalibrering = las_parametrar()
                    st.session_state.kalibrering_meddelande = (
                        "success", f"Kalibreringen är klar och sparad i parametrar.py: {text}.")
        st.rerun()

    if st.session_state.get("kalibrering_meddelande"):
        typ, text = st.session_state.kalibrering_meddelande
        getattr(st, typ)(text)

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

def rita_live_mottagning(avkodare, avkoda, senast_ritat):
    """
    Visar det som hittills tagits emot i mottagarkolumnen.
    Ritar bara om när nya bitar har kommit. Returnerar vad som ritades.
    """
    if avkodare is None:
        lage = ("startar",)
    else:
        bitar = list(avkodare.payload)
        lage = (avkodare.start_index is not None, avkodare.langd, len(bitar))
    if lage == senast_ritat:
        return senast_ritat

    with rx_live.container():
        st.subheader("Tar emot...")
        if avkodare is None:
            st.info("Startar mottagaren...")
            return lage
        if avkodare.start_index is None:
            st.info("Väntar på startsekvens...")
            return lage
        if avkodare.langd is None:
            st.info("Startsekvens hittad. Tar emot längdfältet...")
            return lage
        st.progress(min(1.0, len(bitar) / avkodare.langd), text=f"{len(bitar)} av {avkodare.langd} bitar mottagna")
        if not avkoda:  # Manuell bitsändning avkodas inte
            return lage
        try:
            delvis = delvis_avkodning(bitar)
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
            # Huffman-trädet bygger på träningstexten, så stora och små bokstäver skickas som de är
            original_text = message_input
            
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
                # Ram: startsekvens + längdfält (3 kopior) + nyttobitar, se skicka.bygg_ram
                nyttobitar = data_to_encode
                tx_bits_full = bygg_ram(nyttobitar)
            except Exception as e:
                st.error(f"Kunde inte bygga ramen. Fel: {e}")
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
        tx_bits_full = bygg_ram(clean_bits)

    if trigger_send:
        with st.spinner("Modulerar och sänder signal..."):
            
            mottagar_fel = []
            sandar_fel = []
            redo = threading.Event()
            stopp = threading.Event()
            timeout_s = max(15.0, (len(tx_bits_full)) * step_time_input + 6.0)
            decode_threshold = st.session_state.threshold_val 
            
            # Avkodaren delas mellan mottagartråden och gränssnittet, så att gränssnittet
            # kan visa datan medan den tas emot. Med riktig hårdvara skapar
            # receive_continuous avkodaren (med DAQ:ns verkliga frekvenser) och lägger den här.
            delad_avkodare = []
            sandar_trad = None
            
            if simulate_hw:
                # --- SIMULERINGSLÄGE ---
                sampel_per_bit, sample_rate = samplingsparametrar(step_time_input)
                avkodare = SynkadBitavkodare(sampel_per_bit, threshold=decode_threshold)
                avkodare.ai_rate = sample_rate
                delad_avkodare.append(avkodare)
                
                # Simulerade bitfel läggs bara i nyttodatan, så att startsekvensen och längdfältet hittas
                sim_bits = np.array(tx_bits_full)
                if sim_bitfel_procent > 0:
                    nytta = slice(len(Start_seq) + LANGDFALT_BITAR * LANGDFALT_KOPIOR, len(tx_bits_full))
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
                    except Exception as e:
                        mottagar_fel.append(str(e))
                
                mottagar_trad = threading.Thread(target=simulerad_mottagare, daemon=True)
                mottagar_trad.start()
                
            else:
                # --- RIKTIG HÅRDVARA (MOTTAGARE OCH SÄNDARE I EGNA TRÅDAR) ---
                def lyssna_i_bakgrunden():
                    try:
                        receive_continuous(
                            step_time=step_time_input, channel="Dev1/ai0", 
                            threshold=decode_threshold, timeout_s=timeout_s, ready_event=redo,
                            skickade_bitar=tx_bits_full, stop_event=stopp,
                            avkodare_ut=delad_avkodare, kasta_fel=True
                        )
                    except Exception as e:
                        mottagar_fel.append(str(e))
                        redo.set()
                
                def sand_i_bakgrunden():
                    try:
                        send_binary_list(tx_bits_full, step_time=step_time_input, extra_time=extra_time_input,
                                         channel="Dev1/ao0", stop_event=stopp, visa_graf=False)
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
                    senast_ritat = rita_live_mottagning(delad_avkodare[0] if delad_avkodare else None,
                                                        not is_manual, senast_ritat)
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
            
            # Packa upp resultatet. Nyttobitarna räknas bara om hela ramen (enligt längdfältet) kom fram.
            avkodare = delad_avkodare[0] if delad_avkodare else None
            if avkodare is not None:
                rx_bits = list(avkodare.payload) if avkodare.klar else []
                raw_data = avkodare.data
                ai_rate = avkodare.ai_rate
            else:
                rx_bits, raw_data = [], []
                ai_rate = samplingsparametrar(step_time_input)[1]
                
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
                            st.warning("Fel antal nyttobitar. Längdfältet har troligen flera bitfel.")
                        
                        # 1. Separera Flagga och Data
                        flag = rx_bits[0]
                        payload_bits = rx_bits[1:]
                        
                        if flag == 0:
                            # Data är Text
                            rx_text = huffman_decode(payload_bits, tree)
                        else:
                            # Data är en Fil! Konvertera bits till bytes
                            rx_is_file = True
                            
                            # Hitta gränsen mellan filnamn (text) och data (0x00)
                            rx_filename, rx_file_data = dela_fildata(bitar_till_bytes(payload_bits))
                            if rx_filename is not None:
                                rx_text = f"Mottagen fil: {rx_filename} ({len(rx_file_data)} bytes)"
                            else:
                                rx_text = "[Fel vid läsning: Hittade ingen metadata/filnamn i den avkodade datan]"

                    except Exception as e:
                        rx_text = f"Något blev fel vid avkodning: {e}"
            else:
                rx_text = "[Ingen data mottogs]"
            
            # Var uppstod bitfel? Varje avläst bit i ramen (startsekvens, längdfält och nyttodata)
            # jämförs med den skickade ramen, som felsok.py gör. Tiderna markeras i grafen.
            fel_markeringar = None
            if avkodare is not None and avkodare.start_index is not None and avkodare.medianer:
                mottagen_ram = (np.asarray(avkodare.medianer) >= avkodare.threshold).astype(int)
                k = min(len(mottagen_ram), len(tx_bits_full))
                fel = np.flatnonzero(mottagen_ram[:k] != np.asarray(tx_bits_full[:k]))
                # Mitten av varje felaktig bit i mottagarens tidsaxel
                fel_markeringar = ((np.asarray(avkodare.bitpositioner)[fel] + avkodare.n / 2) / ai_rate).tolist()
            
            # Rådata från fotodetektorn sparas för CSV-export vid både text och fil
            if raw_data:
                rx_times = np.arange(len(raw_data)) / ai_rate
                rx_df = pd.DataFrame({
                    "Time (s)": rx_times, 
                    "Signal (V)": raw_data,
                    "Threshold (V)": [decode_threshold] * len(raw_data)
                }).set_index("Time (s)")
            else:
                rx_df = None
            
            # Styrsignalen till LC-cellen: samma AO-sampel som skicka.py skickar (utglesas innan den visas)
            if tx_bits_full:
                ao_rate = AO_SAMPEL_PER_BIT / step_time_input
                tx_volts = till_spanningar(tx_bits_full)
                tx_df = pd.DataFrame({"Tid (s)": np.arange(len(tx_volts)) / ao_rate,
                                      "Spänning (V)": tx_volts}).set_index("Tid (s)")
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
            
            kalibrering = st.session_state.kalibrering
            kalibreringstext = (f"{kalibrering['tid'] or 'unknown date'}, {1 / kalibrering['step_time']:.0f} bit/s, "
                                f"{kalibrering['threshold']:.2f} V (parametrar.py)")
            
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
                f"Calibration: {kalibreringstext}\n"
                f"Sent message converted to bits (incl seq): {tx_bits_full}\n"
                f"Received bits (excl seq): {rx_bits}\n"
                f"Decoded received message: {rx_text}\n"
                f"Bit errors in frame: {len(fel_markeringar) if fel_markeringar is not None else '-'}\n"
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
                "fel_markeringar": fel_markeringar,
            }

    def visa_sandarsignal(data):
        st.subheader("Skickade Bitar (inkl. startsekvens och längdfält)")
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
    if isinstance(markeringar, dict):  # Sparat av en äldre version med Hamming-kodning
        markeringar = None
    st.altair_chart(signalgraf(data["rx_df"], "Signal (V)", "#21c354",
                               troskel=data.get("decode_threshold"), markeringar=markeringar),
                    use_container_width=True)

    totalt = data.get("rx_punkter_totalt", len(data["rx_df"]))
    text = "Streckad orange linje: tröskelvärde."
    if markeringar:
        text += (f" Röda streck: bitar som avlästes fel ({len(markeringar)} st),"
                 " hittade genom att jämföra med de skickade bitarna.")
    if totalt > len(data["rx_df"]):
        text += f" Visar {len(data['rx_df'])} av {totalt} sampel (min/max per segment)."
    st.caption(text + " All rådata finns i CSV-filen.")

with col_rx:
    if st.session_state.history:
        data = st.session_state.history
        
        st.subheader("Slutgiltigt mottaget meddelande/fil")
        if data.get("rx_is_file") and data.get("rx_file_data") is not None:
            if data.get("jamforelse") is not None:
                visa_kvitto(data["jamforelse"])
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