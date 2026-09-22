import streamlit as st
import pandas as pd
import numpy as np
import time

st.set_page_config(layout="wide", page_title="Optisk Kommunikation Y-Projekt")

# ==========================================
# 1. HÅRDVARU-ABSTRAKTION (BYTS UT SENARE)
# ==========================================
# Dessa funktioner "fakear" hårdvaran. När det riktiga systemet ska 
# implementeras, ersätt innehållet i dessa med bibliotek för DAC/ADC.

def hw_send_data(voltage_array):
    """Simulerar sändningen till LC-cellen via DAC."""
    time.sleep(1) # Simulerar fördröjning i systemet
    return True

def hw_receive_data(sent_voltage_array):
    """
    Simulerar mottagning av data från fotodetektorn via ADC.
    För proof-of-concept lägger vi på lite brus på den skickade signalen.
    """
    noise = np.random.normal(0, 0.5, len(sent_voltage_array))
    received_voltage = sent_voltage_array + noise
    return received_voltage

# ==========================================
# 2. LOGIK OCH SIGNALBEHANDLING
# ==========================================

def text_to_bits(text):
    """Omvandlar text till en sträng av bits."""
    return ''.join(format(ord(c), '08b') for c in text)

def bits_to_text(bits):
    """Omvandlar en sträng av bits tillbaka till text."""
    try:
        chars = [chr(int(bits[i:i+8], 2)) for i in range(0, len(bits), 8)]
        return ''.join(chars)
    except ValueError:
        return "[Kunde inte avkoda signal]"

def generate_bipolar_signal(bits, amplitude=5.0):
    """
    Skapar en bipolär styrsignal (amplitud <= 10V enligt kravspec).
    '1' blir positiv spänning, '0' blir negativ spänning.
    """
    signal = []
    # Skapar 10 datapunkter per bit för att få en snygg graf
    for bit in bits:
        voltage = amplitude if bit == '1' else -amplitude
        signal.extend([voltage] * 10)
    return np.array(signal)

def decode_voltage_to_bits(voltage_array, threshold=0.0):
    """Avkodar den mottagna spänningen tillbaka till bits."""
    bits = ""
    # Läser av ett värde per 10:e datapunkt (mitten av pulsen)
    for i in range(4, len(voltage_array), 10):
        bits += '1' if voltage_array[i] > threshold else '0'
    return bits

# ==========================================
# 3. ANVÄNDARGRÄNSSNITT (STREAMLIT)
# ==========================================

st.title("Optisk Kommunikation - Kontrollpanel")
st.markdown("---")

# Initiera state-variabler för att hålla data mellan uppdateringar
if "status" not in st.session_state:
    st.session_state.update({
        "status": "Väntar på data...",
        "sent_bits": "",
        "tx_signal": [],
        "rx_signal": [],
        "received_bits": "",
        "received_text": ""
    })

# Skapa den tvådelade layouten
col_send, col_receive = st.columns(2)

# --- SÄNDARSIDA (VÄNSTER) ---
with col_send:
    st.header("📤 Sändare (LC-Cell)")
    
    input_text = st.text_input("Data att skicka:", placeholder="Skriv in ett ord eller en mening...")
    voltage_amplitude = st.slider("Drivspänning (V)", min_value=1.0, max_value=10.0, value=5.0, step=0.5)
    
    if st.button("Skicka Data", use_container_width=True, type="primary"):
        if input_text:
            with st.spinner("Modulerar och sänder signal..."):
                # 1. Omvandla och generera signal
                bits = text_to_bits(input_text)
                tx_signal = generate_bipolar_signal(bits, amplitude=voltage_amplitude)
                
                # 2. Skicka till hårdvara (simulerat)
                hw_send_data(tx_signal)
                
                # 3. Uppdatera gränssnittets tillstånd
                st.session_state.sent_bits = bits
                st.session_state.tx_signal = tx_signal
                
                # 4. Trigga mottagaren (simulerat)
                rx_signal = hw_receive_data(tx_signal)
                st.session_state.rx_signal = rx_signal
                
                # 5. Avkoda signal
                rx_bits = decode_voltage_to_bits(rx_signal)
                st.session_state.received_bits = rx_bits
                st.session_state.received_text = bits_to_text(rx_bits)
                
                st.session_state.status = "Data överförd!"
        else:
            st.warning("Vänligen mata in text innan du skickar.")

    st.divider()
    st.subheader("Signalomvandling")
    st.text_area("Genererade Bits:", value=st.session_state.sent_bits, height=100, disabled=True)
    
    st.subheader("Styrsignal till LC-Cell (Realtid)")
    if len(st.session_state.tx_signal) > 0:
        df_tx = pd.DataFrame(st.session_state.tx_signal, columns=["Spänning (V)"])
        st.line_chart(df_tx, color="#ff4b4b")
    else:
        st.info("Ingen signal skickad ännu.")

# --- MOTTAGARSIDA (HÖGER) ---
with col_receive:
    st.header("📥 Mottagare (Fotodetektor)")
    
    # Skapa en tom behållare för layoutens skull, för att matcha sändarsidans knappar
    st.markdown("<br><br><br>", unsafe_allow_html=True)
    st.success(f"Status: {st.session_state.status}")
    
    st.divider()
    st.subheader("Fotodetektor Spänning (Realtid)")
    if len(st.session_state.rx_signal) > 0:
        df_rx = pd.DataFrame(st.session_state.rx_signal, columns=["Uppmätt Spänning (V)"])
        st.line_chart(df_rx, color="#00cc96")
    else:
        st.info("Väntar på inkommande signal.")
        
    st.subheader("Signalomvandling och Avkodning")
    st.text_area("Avkodade Bits:", value=st.session_state.received_bits, height=100, disabled=True)
    
    st.metric(label="Mottaget Meddelande", value=st.session_state.received_text if st.session_state.received_text else "-")

    # Ladda ner data-funktion enligt kravspec (All information lagras på fil)
    if st.session_state.received_text:
        log_data = f"Skickat: {st.session_state.sent_bits}\nMottaget: {st.session_state.received_text}"
        st.download_button(
            label="💾 Spara mottagen data till fil",
            data=log_data,
            file_name="optkom_log.txt",
            mime="text/plain",
            use_container_width=True
        )