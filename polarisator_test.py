import queue
import sys
import threading
import time

import matplotlib.pyplot as plt
import nidaqmx
import numpy as np

from nidaqmx.constants import (
    AcquisitionType,
    RegenerationMode,
    READ_ALL_AVAILABLE,
    TerminalConfiguration,
)


# ============================================================
# KONFIGURATION
# ============================================================

DEVICE = "Dev1"

AO_CHANNEL = f"{DEVICE}/ao0"
AI_CHANNELS = [
    f"{DEVICE}/ai0",   # TIA / fotodiod
    f"{DEVICE}/ai1",   # komparator
]

# LC-cell
sample_rate = 2000.0       # Hz
lc_frequency = 10.0        # Hz
lc_amplitude = 5.0         # V peak -> signalen blir -5 ... +5 V
waveform = "sine"           # "sine" eller "square"

# Hur AI1 ska tolkas som 0/1.
# Om komparatorn ger 0...5 V är 2.5 V lämpligt.
# Om den ger t.ex. -5...+5 V, använd 0 V.
comparator_logic_threshold = 2.5

PLOT_SECONDS = 2.0

cmd_queue = queue.Queue()


# ============================================================
# TERMINALINMATNING
# ============================================================

def input_thread(q):
    print("""
Kommandon:

    f 20        LC-frekvens = 20 Hz
    a 3         LC-amplitud = ±3 V
    s 4000      samplingsfrekvens = 4000 Hz

    w sine      sinus
    w square    fyrkantvåg

    ct 2.5      gräns för att tolka AI1 som 0/1

    dark        spara mörkernivå på AI0
    max         spara ljusnivå för parallella polarisatorer
    min         spara ljusnivå för korsade polarisatorer

    zero        LC-utgång = 0 V
    q           avsluta
""")

    while True:
        try:
            line = sys.stdin.readline()

            if not line:
                break

            parts = line.strip().lower().split()

            if not parts:
                continue

            cmd = parts[0]

            if cmd in ["q", "quit", "exit"]:
                q.put(("quit", None))
                break

            elif cmd in ["dark", "max", "min", "zero"]:
                q.put((cmd, None))

            elif len(parts) == 2:

                if cmd in ["w", "wave"]:
                    q.put(("wave", parts[1]))

                else:
                    try:
                        value = float(parts[1])
                    except ValueError:
                        print("Felaktigt tal.")
                        continue

                    if cmd in ["f", "freq"]:
                        q.put(("freq", value))

                    elif cmd in ["a", "amp"]:
                        q.put(("amp", value))

                    elif cmd in ["s", "rate", "sr"]:
                        q.put(("rate", value))

                    elif cmd in ["ct", "threshold"]:
                        q.put(("threshold", value))

                    else:
                        print("Okänt kommando.")

            else:
                print("Okänt kommando.")

        except Exception as e:
            print(f"Fel vid terminalinläsning: {e}")
            break


# ============================================================
# LC-VÅGFORM
# ============================================================

def create_waveform(rate, frequency, amplitude, waveform_type):

    samples_per_cycle = int(round(rate / frequency))

    # Gör fyrkantvågen symmetrisk så att medelvärdet blir exakt 0
    if samples_per_cycle % 2:
        samples_per_cycle += 1

    samples_per_cycle = max(samples_per_cycle, 4)

    if waveform_type == "square":

        half = samples_per_cycle // 2

        data = np.concatenate([
            np.full(half, amplitude),
            np.full(half, -amplitude)
        ])

    else:

        t = np.arange(samples_per_cycle) / rate

        data = amplitude * np.sin(
            2 * np.pi * frequency * t
        )

    actual_frequency = rate / samples_per_cycle

    return data, actual_frequency


# ============================================================
# DAQ SETUP
# ============================================================

def setup_tasks(rate, frequency, amplitude, waveform_type):

    ao_task = nidaqmx.Task()
    ai_task = nidaqmx.Task()

    # --------------------------------------------------------
    # AO0 - LC-cell
    # --------------------------------------------------------

    ao_task.ao_channels.add_ao_voltage_chan(
        AO_CHANNEL,
        min_val=-10.0,
        max_val=10.0,
    )

    ao_data, actual_frequency = create_waveform(
        rate,
        frequency,
        amplitude,
        waveform_type,
    )

    ao_task.timing.cfg_samp_clk_timing(
        rate=rate,
        sample_mode=AcquisitionType.CONTINUOUS,
        samps_per_chan=len(ao_data),
    )

    ao_task.out_stream.regen_mode = (
        RegenerationMode.ALLOW_REGENERATION
    )

    ao_task.write(
        ao_data,
        auto_start=False,
    )

    # --------------------------------------------------------
    # AI0 = TIA
    # AI1 = comparator
    # --------------------------------------------------------

    for channel in AI_CHANNELS:

        ai_task.ai_channels.add_ai_voltage_chan(
            channel,
            terminal_config=TerminalConfiguration.RSE,
            min_val=-10.0,
            max_val=10.0,
        )

    ai_task.timing.cfg_samp_clk_timing(
        rate=rate,
        sample_mode=AcquisitionType.CONTINUOUS,
        samps_per_chan=int(rate * 2),
    )

    ai_task.in_stream.input_buf_size = int(rate * 10)

    # Börja mäta innan LC-utgången startas
    ai_task.start()
    ao_task.start()

    return ao_task, ai_task, actual_frequency


def close_tasks(ao_task, ai_task):

    for task in [ao_task, ai_task]:

        if task is not None:

            try:
                task.stop()
            except Exception:
                pass

            try:
                task.close()
            except Exception:
                pass


def set_output_zero():

    try:

        with nidaqmx.Task() as task:

            task.ao_channels.add_ao_voltage_chan(
                AO_CHANNEL,
                min_val=-10.0,
                max_val=10.0,
            )

            task.write(0.0)

        print("AO0 = 0 V")

    except Exception as e:

        print(f"Kunde inte nollställa AO0: {e}")


# ============================================================
# STARTA TERMINALTRÅD
# ============================================================

threading.Thread(
    target=input_thread,
    args=(cmd_queue,),
    daemon=True,
).start()


# ============================================================
# STARTA DAQ
# ============================================================

ao_task, ai_task, actual_frequency = setup_tasks(
    sample_rate,
    lc_frequency,
    lc_amplitude,
    waveform,
)


# ============================================================
# MÄTVÄRDEN FÖR POLARISATORTEST
# ============================================================

dark_level = None
imax = None
imin = None

latest_ai0_mean = 0.0


# ============================================================
# PLOT
# ============================================================

plt.ion()

fig, ax = plt.subplots(figsize=(10, 5))

N = int(sample_rate * PLOT_SECONDS)

xdata = (
    np.arange(N) / sample_rate
    - PLOT_SECONDS
)

y_tia = np.zeros(N)
y_comp = np.zeros(N)

line_tia, = ax.plot(
    xdata,
    y_tia,
    label="AI0: TIA / fotodiod"
)

line_comp, = ax.plot(
    xdata,
    y_comp,
    label="AI1: komparator"
)

ax.set_xlim(-PLOT_SECONDS, 0)
ax.set_ylim(-10, 10)

ax.set_xlabel("Tid [s]")
ax.set_ylabel("Spänning [V]")

ax.grid(True)
ax.legend(loc="upper right")


# ============================================================
# HUVUDLOOP
# ============================================================

running = True

try:

    while running:

        reconfigure = False

        # ----------------------------------------------------
        # Läs terminalkommandon
        # ----------------------------------------------------

        while not cmd_queue.empty():

            command, value = cmd_queue.get()

            if command == "quit":

                running = False
                break

            elif command == "freq":

                if value < 1:
                    print("LC-frekvensen ska vara minst 1 Hz.")

                elif value >= sample_rate / 2:
                    print("Frekvensen är för hög för aktuell sample rate.")

                else:
                    lc_frequency = value
                    reconfigure = True

            elif command == "amp":

                if 0 <= value <= 10:

                    lc_amplitude = value
                    reconfigure = True

                else:

                    print("Amplituden måste vara 0 ... 10 V.")

            elif command == "rate":

                # AO på USB-6003 klarar max 5 kS/s
                if 100 <= value <= 5000:

                    sample_rate = value

                    if lc_frequency < sample_rate / 2:
                        reconfigure = True
                    else:
                        print(
                            "Sample rate är för låg för vald LC-frekvens."
                        )

                else:

                    print(
                        "Använd sample rate mellan 100 och 5000 Hz."
                    )

            elif command == "wave":

                if value in ["sine", "square"]:

                    waveform = value
                    reconfigure = True

                else:

                    print("Välj 'sine' eller 'square'.")

            elif command == "threshold":

                comparator_logic_threshold = value

                print(
                    f"AI1 tolkningsgräns = "
                    f"{comparator_logic_threshold:.2f} V"
                )

            elif command == "zero":

                lc_amplitude = 0.0
                reconfigure = True

            # ------------------------------------------------
            # Polarisatormätningar
            # ------------------------------------------------

            elif command == "dark":

                dark_level = latest_ai0_mean

                print(
                    f"Mörkernivå sparad: "
                    f"{dark_level:.5f} V"
                )

            elif command == "max":

                if dark_level is None:
                    print(
                        "Gör först 'dark' med lasern blockerad."
                    )
                else:
                    imax = abs(
                        latest_ai0_mean - dark_level
                    )

                    print(
                        f"Imax = {imax:.5f} V"
                    )

            elif command == "min":

                if dark_level is None:
                    print(
                        "Gör först 'dark' med lasern blockerad."
                    )

                else:

                    imin = abs(
                        latest_ai0_mean - dark_level
                    )

                    print(
                        f"Imin = {imin:.5f} V"
                    )

            # ------------------------------------------------
            # Beräkna polarisation när både max/min finns
            # ------------------------------------------------

            if (
                imax is not None
                and imin is not None
                and command in ["max", "min"]
            ):

                high = max(imax, imin)
                low = min(imax, imin)

                if high + low > 0:

                    degree = (
                        (high - low)
                        / (high + low)
                    )

                    print(
                        f"\nPolarisationsgrad: "
                        f"{100 * degree:.2f} %"
                    )

                if low > 0:

                    extinction = high / low

                    extinction_db = (
                        10 * np.log10(extinction)
                    )

                    print(
                        f"Extinktionsförhållande: "
                        f"{extinction:.1f}:1"
                    )

                    print(
                        f"Extinktionsförhållande: "
                        f"{extinction_db:.2f} dB\n"
                    )

        if not running:
            break

        # ----------------------------------------------------
        # Starta om DAQ om LC-parametrarna ändrats
        # ----------------------------------------------------

        if reconfigure:

            close_tasks(
                ao_task,
                ai_task
            )

            N = int(
                sample_rate
                * PLOT_SECONDS
            )

            xdata = (
                np.arange(N)
                / sample_rate
                - PLOT_SECONDS
            )

            y_tia = np.zeros(N)
            y_comp = np.zeros(N)

            line_tia.set_xdata(xdata)
            line_tia.set_ydata(y_tia)

            line_comp.set_xdata(xdata)
            line_comp.set_ydata(y_comp)

            ax.set_xlim(
                -PLOT_SECONDS,
                0
            )

            ao_task, ai_task, actual_frequency = (
                setup_tasks(
                    sample_rate,
                    lc_frequency,
                    lc_amplitude,
                    waveform,
                )
            )

            print(
                "\nLC-cell:"
                f"\n  vågform: {waveform}"
                f"\n  amplitud: ±{lc_amplitude:.2f} V"
                f"\n  begärd frekvens: {lc_frequency:.3f} Hz"
                f"\n  faktisk frekvens: {actual_frequency:.3f} Hz"
                f"\n  sample rate: {sample_rate:.0f} Hz\n"
            )

        # ----------------------------------------------------
        # Läs AI
        # ----------------------------------------------------

        data = ai_task.read(
            number_of_samples_per_channel=READ_ALL_AVAILABLE,
            timeout=1.0,
        )

        if (
            isinstance(data, list)
            and len(data) >= 2
            and len(data[0]) > 0
        ):

            new_tia = np.asarray(data[0])
            new_comp = np.asarray(data[1])

            num_new = len(new_tia)

            # Medelvärde från senaste blocket
            latest_ai0_mean = np.mean(new_tia)

            # Comparator 0/1
            comp_state = int(
                np.mean(new_comp)
                > comparator_logic_threshold
            )

            # -----------------------------------------------
            # Uppdatera plotbuffert
            # -----------------------------------------------

            if num_new >= N:

                y_tia = new_tia[-N:]
                y_comp = new_comp[-N:]

            else:

                y_tia = np.roll(
                    y_tia,
                    -num_new
                )

                y_comp = np.roll(
                    y_comp,
                    -num_new
                )

                y_tia[-num_new:] = new_tia
                y_comp[-num_new:] = new_comp

            line_tia.set_ydata(y_tia)
            line_comp.set_ydata(y_comp)

            ax.set_title(
                f"LC: ±{lc_amplitude:.2f} V, "
                f"{actual_frequency:.2f} Hz "
                f"({waveform})   |   "
                f"AI0 = {latest_ai0_mean:.3f} V   |   "
                f"Comparator = {comp_state}"
            )

            fig.canvas.draw()
            fig.canvas.flush_events()

        time.sleep(0.01)


except KeyboardInterrupt:

    print("\nAvbryter...")


finally:

    close_tasks(
        ao_task,
        ai_task
    )

    set_output_zero()

    plt.ioff()
    plt.close("all")
