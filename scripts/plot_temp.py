#!/usr/bin/env python3
"""
Live plotter for Arduino Mega2560 + MAX31856 (+ optional 2nd sensor) + Valve.

Reads CSV lines from serial:
  - 3 columns:  time_s,temp_C,valve
  - 4 columns:  time_s,temp1_C,temp2_C,valve   (backward compatible)

Features:
  - Auto-detects 1 or 2 temperature channels from incoming rows
  - Plots temperatures vs time
  - Overlays valve state on a second y-axis
  - Red dashed set-point line (default: -10 °C)
"""

import sys
import time
from collections import deque

import serial
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

# === Config ===
PORT = sys.argv[1] if len(sys.argv) > 1 else '/dev/ttyACM0'
BAUD = 115200
SETPOINT_C = 25.0        # match your Arduino control setpoint
MAXLEN = 600              # ~10 minutes @ 1 Hz

# === Serial setup ===
ser = serial.Serial(PORT, BAUD, timeout=0.1)
time.sleep(2)             # allow Arduino reset
ser.reset_input_buffer()

# === Data buffers ===
times = deque(maxlen=MAXLEN)
temps = [deque(maxlen=MAXLEN), deque(maxlen=MAXLEN)]  # support up to 2
valves = deque(maxlen=MAXLEN)

# Runtime state: how many temperature columns are present (1 or 2)
num_temp_cols = None

# === Plot setup ===
fig, ax1 = plt.subplots()
ax2 = ax1.twinx()

# Pre-create up to 2 temperature lines; we'll show/hide as needed
colors = ['blue', 'orange']
labels = ['Temp1 (°C)', 'Temp2 (°C)']
temp_lines = [ax1.plot([], [], color=colors[i], label=labels[i], lw=2)[0] for i in range(2)]
for line in temp_lines:
    line.set_visible(False)  # will enable once we know how many temps we have

# Valve line
line_valve, = ax2.step([], [], 'gray', label='Valve (0/1)', where='post')

# Set-point line
hline = ax1.axhline(SETPOINT_C, color='red', linestyle='--', label=f'Set-point ({SETPOINT_C:g} °C)')

# Axes labels/limits
ax1.set_xlabel('Time (s)')
ax1.set_ylabel('Temperature (°C)')
ax2.set_ylabel('Valve State')
ax1.set_ylim(min(-10.0, SETPOINT_C - 5), max(50.0, SETPOINT_C + 5))
ax2.set_ylim(-0.1, 1.1)

# Legends
leg1 = ax1.legend(loc='upper left')
leg2 = ax2.legend(loc='upper right')

def _try_parse_row(raw: str):
    """Parse one CSV row. Returns (t, temps_list, valve) or None on failure."""
    parts = raw.strip().split(',')
    if len(parts) not in (3, 4):
        return None
    try:
        t = float(parts[0])
        if len(parts) == 3:
            tvals = [float(parts[1])]
            valve = int(parts[2])
        else:
            tvals = [float(parts[1]), float(parts[2])]
            valve = int(parts[3])
    except ValueError:
        return None
    return (t, tvals, valve)

# === Animation init ===
def init():
    for line in temp_lines:
        line.set_data([], [])
    line_valve.set_data([], [])
    return temp_lines + [line_valve, hline]

# === Animation update ===
def update(_frame):
    global num_temp_cols

    latest = None
    # Read all currently buffered lines; keep the latest complete row
    while ser.in_waiting:
        raw = ser.readline().decode('utf-8', errors='ignore').strip()
        if not raw:
            continue
        # Skip header line if Arduino prints one
        if raw.lower().startswith('time'):
            continue
        parsed = _try_parse_row(raw)
        if parsed:
            latest = parsed

    if latest is None:
        return temp_lines + [line_valve, hline]

    t, tvals, valve = latest

    # Detect number of temp columns once
    if num_temp_cols is None:
        num_temp_cols = len(tvals)
        for i in range(2):
            temp_lines[i].set_visible(i < num_temp_cols)
        # refresh legends to show only visible lines
        handles1 = [hline] + [ln for ln in temp_lines if ln.get_visible()]
        labels1 = [h.get_label() for h in handles1]
        ax1.legend(handles1, labels1, loc='upper left')
        fig.canvas.draw_idle()

    # Append new data
    times.append(t)
    if num_temp_cols >= 1:
        temps[0].append(tvals[0])
    if num_temp_cols == 2:
        temps[1].append(tvals[1])
    valves.append(valve)

    # Update lines
    if num_temp_cols >= 1:
        temp_lines[0].set_data(times, temps[0])
    if num_temp_cols == 2:
        temp_lines[1].set_data(times, temps[1])
    line_valve.set_data(times, valves)

    # Autoscale X to data range
    if times:
        ax1.set_xlim(times[0], times[-1])

    return temp_lines + [line_valve, hline]

# === Run ===
ani = FuncAnimation(
    fig, update, init_func=init,
    interval=1000, blit=True, cache_frame_data=False
)

try:
    plt.show()
finally:
    ser.close()
