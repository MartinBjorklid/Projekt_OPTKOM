"""Gemensamma länkparametrar. Importeras av skicka.py, taemot.py och program.py.

Skrivs över av kalibrering.py men kan också ändras för hand.
"""

# Kalibrerad 2026-11-03 12:54: vilonivå 0.325 V, högsta uppmätta 4.672 V,
# godkända trösklar 2.560–4.200 V (marginal 0.067 V), klarade 3000 bitar.
step_time = 1 / 270  # sekunder per bit (270 Hz)
threshold = 3.38  # V; AI-sampel >= threshold tolkas som 1
