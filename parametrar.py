"""Gemensamma länkparametrar. Importeras av skicka.py, taemot.py och program.py.

Skrivs av kalibrering.py (step_time, threshold) och drivfrekvens.py
(drivfrekvens) men kan också ändras för hand.
"""

step_time = 1 / 270  # sekunder per bit (270 Hz); kalibrerad 2026-11-10 16:35, klarade 3000 bitar
threshold = 3.06  # V; AI-sampel >= threshold tolkas som 1; godkänt 2.400–3.720 V, vila 0.176 V, max 4.242 V
drivfrekvens = 1250  # Hz; växelspänningen som driver LC-kristallen under en etta; mätt 2026-11-10 16:30, LC-modulatorn klarar max 400 bit/s
drivamplitud = 5.0  # V; toppvärdet på växelspänningen (+/- drivamplitud); mätt 2026-11-10 16:32, kristallen slår om från ca 2 V
