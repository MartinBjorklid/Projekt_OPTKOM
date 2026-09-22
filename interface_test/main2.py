import streamlit as st
import numpy as np
import pandas as pd
import time

# ==============================================================================
# HÅRDVARU-ABSTRAKTION (Byt ut klassens innehåll mot det fysiska systemet)
# ==============================================================================
class OpticalSystemSimulator:
    def __init__(self, amplitude=10.0):
        # Enligt kravspecifikationen får LC-cellens styrsignal inte överstiga 10V (bipolär)
        self.amplitude = amplitude 

    def text_to_bits(self, text):
        return ''.join(format(ord(c), '08b') for c in text)

    def bits_to_text(self, bits):
        # Delar upp bitsträngen i grupper om 8 och konverterar tillbaka till tecken
        chars = [chr(int(bits[i:i+8], 2)) for i in range(0, len(bits), 8) if len(bits[i:i+8]) == 8]
        return ''.join(chars)

    def generate_lc_voltage(self, bit_string):
        """Simulerar styrsignalen till LC-cellen."""
        signal = []
        for bit in bit_string:
            # Bipolär signal: '1' -> +10V, '0' -> -10V
            voltage = self.amplitude if bit == '1' else -self.amplitude
            # Skapar 10 mätpunkter per bit för att få en tydlig graf i realtid
            signal.extend([voltage] * 10) 
        return np.array(signal)

    def read_photodetector_voltage(self, lc_signal):
        """Simulerar avläsning från fotodetektorn, inklusive brus."""
        time.sleep(0.5) # Simulerar systemfördröjning
        noise = np.random.normal(0, 0.3, len(lc_signal))
        # Fotodetektorn ger ofta en unipolär spänning, här simulerad från 0V till 5V
        rx_signal = (lc_signal > 0).astype(float) * 5.0 + noise
        return rx_signal

    def decode_voltage_to_bits(self, rx_signal):
        """Avkodar mottagen spänning tillbaka till bits."""
        # Tröskelvärde på 2.5V för att avgöra om det är en 1:a eller 0:a
        bit_samples = rx_signal[::10] 
        return ''.join(['1' if v > 2.5 else '0' for v in bit_samples])


# ==============================================================================
# ANVÄNDARGRÄNSSNITT (Visas på skärmen i realtid)
# ==============================================================================
st.set_page_config(page_title="Optisk Kommunikation", layout="wide")

# Initiera system och state
if 'hw' not in st.session_state:
    st.session_state.hw = OpticalSystemSimulator(amplitude=10.0)
if 'history' not in st.session_state:
    st.session_state.history = None

st.title("Optisk Kommunikation - Kontrollpanel")
st.markdown("---")

col_tx, col_rx = st.columns(2)

with col_tx:
    st.header("Sändare (LC-cell)")
    message_input = st.text_input("Mata in meddelande att skicka:", placeholder="Skriv ditt ord här...")
    
    if st.button("Skicka Data", use_container_width=True):
        if message_input:
            with st.spinner("Modulerar och sänder signal..."):
                hw = st.session_state.hw
                
                # 1. Konvertera data
                tx_bits = hw.text_to_bits(message_input)
                tx_voltage = hw.generate_lc_voltage(tx_bits)
                
                # 2. Ta emot data
                rx_voltage = hw.read_photodetector_voltage(tx_voltage)
                rx_bits = hw.decode_voltage_to_bits(rx_voltage)
                rx_text = hw.bits_to_text(rx_bits)
                
                # Spara till session state för rendering
                st.session_state.history = {
                    "original_text": message_input,
                    "tx_bits": tx_bits,
                    "tx_voltage": tx_voltage,
                    "rx_voltage": rx_voltage,
                    "rx_bits": rx_bits,
                    "rx_text": rx_text
                }
        else:
            st.warning("Vänligen mata in ett meddelande först.")

    if st.session_state.history:
        st.subheader("Skickade Bitar")
        st.code(st.session_state.history["tx_bits"])
        st.subheader("Styrsignal till LC-cell (Volt)")
        st.line_chart(st.session_state.history["tx_voltage"], color="#ff4b4b")

with col_rx:
    st.header("Mottagare (Fotodetektor)")
    
    if st.session_state.history:
        data = st.session_state.history
        
        st.subheader("Slutgiltigt mottaget meddelande")
        if data["original_text"] == data["rx_text"]:
            st.success(f"**{data['rx_text']}**")
        else:
            st.error(f"**{data['rx_text']}** (Överföringsfel upptäcktes)")
            
        st.subheader("Mottagna Bitar")
        st.code(data["rx_bits"])
        
        st.subheader("Fotodetektorns utsignal (Volt)")
        st.line_chart(data["rx_voltage"], color="#21c354")
        
        # Möjlighet att lagra data på fil
        export_df = pd.DataFrame({
            "Tidspunkt (simulerad)": range(len(data["tx_voltage"])),
            "LC_Spänning (V)": data["tx_voltage"],
            "Fotodetektor_Spänning (V)": data["rx_voltage"],
            "Skickat Meddelande": message_input,
            "Mottaget Meddelande": hw.text_to_bits(message_input)
        })
        st.download_button(
            label="💾 Spara mätdata till CSV",
            data=export_df.to_csv(index=False).encode('utf-8'),
            file_name="optisk_overforing_data.csv",
            mime="text/csv",
            use_container_width=True
        )
    else:
        st.info("Väntar på inkommande signal...")