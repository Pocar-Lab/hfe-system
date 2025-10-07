#!/usr/bin/env python3
"""
Live plotter for Mega2560 + 9× MAX31856 + valve override UI.

Arduino CSV (1 Hz):
  time_s, temp0_C, ... temp8_C, valve, mode
where valve ∈ {0,1}, mode ∈ {'A','O','C'} for AUTO / OPEN / CLOSE.
"""

import time, math
from collections import deque
import serial
import tkinter as tk
from tkinter import ttk

import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.animation import FuncAnimation

# ======= Config =======
PORT = '/dev/ttyACM0'   # <-- change if needed (e.g., 'COM5' on Windows)
BAUD = 115200
NUM_SENSORS = 9
SETPOINT = 25.0
WINDOW_LEN = 600  # 10 minutes @ 1 Hz

# ======= Serial =======
ser = serial.Serial(PORT, BAUD, timeout=0.1)
time.sleep(2)              # allow Arduino to reset
ser.reset_input_buffer()

# ======= Data buffers =======
times   = deque(maxlen=WINDOW_LEN)
temps   = [deque(maxlen=WINDOW_LEN) for _ in range(NUM_SENSORS)]

# ======= Tk window =======
root = tk.Tk()
root.title("TC Monitor (9x MAX31856) + Valve Control")

# Left: plot
fig, ax = plt.subplots()
lines = []
for i in range(NUM_SENSORS):
    (line_i,) = ax.plot([], [], '-', lw=2, label=f'TC{i}')
    lines.append(line_i)

hline = ax.axhline(SETPOINT, linestyle='--', label=f'Set-point ({SETPOINT} °C)')

ax.set_xlabel('Time (s)')
ax.set_ylabel('Temperature (°C)')
ax.set_ylim(0, 50)
ax.legend(loc='upper left')

canvas = FigureCanvasTkAgg(fig, master=root)
canvas_widget = canvas.get_tk_widget()
canvas_widget.grid(row=0, column=0, sticky="nsew")

# Right: controls
side = ttk.Frame(root, padding=12)
side.grid(row=0, column=1, sticky="ns")
root.columnconfigure(0, weight=1)
root.rowconfigure(0, weight=1)

ttk.Label(side, text="Valve State", font=("TkDefaultFont", 12, "bold")).grid(row=0, column=0, pady=(0,4), sticky="w")
state_var = tk.StringVar(value="—")
state_lbl = tk.Label(side, textvariable=state_var, width=12, relief="groove",
                     font=("TkDefaultFont", 18, "bold"))
state_lbl.grid(row=1, column=0, pady=(0,12), sticky="we")

mode_var = tk.StringVar(value="Mode: AUTO")
ttk.Label(side, textvariable=mode_var).grid(row=2, column=0, pady=(0,12), sticky="w")

avg_var = tk.StringVar(value="Avg (valid): —")
ttk.Label(side, textvariable=avg_var).grid(row=3, column=0, pady=(0,12), sticky="w")

btn_frame = ttk.LabelFrame(side, text="Override")
btn_frame.grid(row=4, column=0, sticky="we", padx=0, pady=(0,12))
btn_frame.columnconfigure(0, weight=1)

def send_cmd(cmd: str):
    try:
        ser.write((cmd + "\n").encode("ascii"))
    except Exception:
        pass

ttk.Button(btn_frame, text="OPEN",  command=lambda: send_cmd("VALVE OPEN")).grid(row=0, column=0, sticky="we", pady=2)
ttk.Button(btn_frame, text="CLOSE", command=lambda: send_cmd("VALVE CLOSE")).grid(row=1, column=0, sticky="we", pady=2)
ttk.Button(btn_frame, text="AUTO",  command=lambda: send_cmd("VALVE AUTO")).grid(row=2, column=0, sticky="we", pady=2)

# ======= Animation / update =======
def init():
    for ln in lines:
        ln.set_data([], [])
    return (*lines, hline)

def parse_line(raw: str):
    """Return tuple (t, temps[9], valve:int, mode_char) or None if malformed."""
    parts = raw.split(',')
    expected = 1 + NUM_SENSORS + 2  # time + N temps + valve + mode
    if len(parts) != expected:
        return None
    try:
        t = float(parts[0])
        vals = []
        for i in range(NUM_SENSORS):
            s = parts[1 + i].strip().lower()
            vals.append(float('nan') if s == 'nan' or s == '' else float(s))
        valve = int(parts[1 + NUM_SENSORS])
        mode_char = parts[2 + NUM_SENSORS].strip()[:1]
        return t, vals, valve, mode_char
    except ValueError:
        return None

def update(_frame):
    latest = None
    # Drain serial buffer, keep only the last complete CSV line
    while ser.in_waiting:
        raw = ser.readline().decode('utf-8', errors='ignore').strip()
        if not raw:
            continue
        # skip header lines or comments Arduino might print (e.g., "#F0=0x..")
        if raw.startswith("time_s") or raw.startswith("#"):
            continue
        parsed = parse_line(raw)
        if parsed:
            latest = parsed

    if latest is None:
        return (*lines, hline)

    t, vals, valve, mode_char = latest

    # append data
    times.append(t)
    for i, v in enumerate(vals):
        temps[i].append(v)

    # update plot lines
    for i, ln in enumerate(lines):
        ln.set_data(times, temps[i])

    # x-limits
    if times:
        ax.set_xlim(times[0], times[-1])

    # compute average of valid sensors for display
    valid = [v for v in vals if isinstance(v, float) and math.isfinite(v)]
    if valid:
        avg = sum(valid) / len(valid)
        avg_var.set(f"Avg (valid {len(valid)}): {avg:.2f} °C")
    else:
        avg_var.set("Avg (valid 0): —")

    # update side labels
    state_var.set("OPEN" if valve else "CLOSED")
    state_lbl.configure(bg=("green" if valve else "light gray"))
    mode_text = {"A": "AUTO", "O": "FORCED OPEN", "C": "FORCED CLOSE"}.get(mode_char.upper(), "AUTO")
    mode_var.set(f"Mode: {mode_text}")

    canvas.draw_idle()
    return (*lines, hline)

ani = FuncAnimation(fig, update, init_func=init, interval=1000, blit=False, cache_frame_data=False)

def on_close():
    try:
        ser.close()
    except Exception:
        pass
    root.destroy()

root.protocol("WM_DELETE_WINDOW", on_close)
root.mainloop()
