#!/usr/bin/env python3
"""
Live plotter for Arduino Mega2560 + MAX6675 (4x) + Valve control.

– Reads CSV lines: “time_s,temp1_C,temp2_C,temp3_C,temp4_C,valve”
– Plots all 4 temperatures vs. time
– Overlays valve state on second y-axis
– Draws a red dashed set-point line at 25 °C
"""

import time
from collections import deque
import serial
import matplotlib
matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

# === Serial port setup ===
PORT = '/dev/ttyACM0'
BAUD = 115200

ser = serial.Serial(PORT, BAUD, timeout=0.1)
time.sleep(2)
ser.reset_input_buffer()

# === Data buffers (10 min at 1 Hz) ===
MAXLEN = 600
times  = deque(maxlen=MAXLEN)
temps  = [deque(maxlen=MAXLEN) for _ in range(4)]
valves = deque(maxlen=MAXLEN)

# === Plot setup ===
fig, ax1 = plt.subplots()
ax2 = ax1.twinx()

# Colors for each temperature line
colors = ['blue', 'orange', 'green', 'purple']
labels = [f'Temp{i+1} (°C)' for i in range(4)]
temp_lines = [ax1.plot([], [], color=c, label=l, lw=2)[0] for c, l in zip(colors, labels)]

# Valve plot
line_valve, = ax2.step([], [], 'gray', label='Valve (0/1)', where='post')

# Setpoint line
hline = ax1.axhline(25.0, color='red', linestyle='--', label='Set-point (25 °C)')

# Axis labels and limits
ax1.set_xlabel('Time (s)')
ax1.set_ylabel('Temperature (°C)')
ax2.set_ylabel('Valve State')

ax1.set_ylim(0, 50)
ax2.set_ylim(-0.1, 1.1)

# Legends
ax1.legend(loc='upper left')
ax2.legend(loc='upper right')

# === Plot initialization ===
def init():
    for line in temp_lines:
        line.set_data([], [])
    line_valve.set_data([], [])
    return temp_lines + [line_valve, hline]

# === Update function ===
def update(frame):
    latest = None
    while ser.in_waiting:
        raw = ser.readline().decode('utf-8', errors='ignore').strip()
        if raw:
            latest = raw

    if not latest:
        return temp_lines + [line_valve, hline]

    parts = latest.split(',')
    if len(parts) != 6:
        return temp_lines + [line_valve, hline]

    try:
        t = float(parts[0])
        temp_values = [float(parts[i+1]) for i in range(4)]
        valve_state = int(parts[5])
    except ValueError:
        return temp_lines + [line_valve, hline]

    # Append to buffers
    times.append(t)
    for i in range(4):
        temps[i].append(temp_values[i])
    valves.append(valve_state)

    # Update line data
    for i, line in enumerate(temp_lines):
        line.set_data(times, temps[i])
    line_valve.set_data(times, valves)

    # Autoscale X-axis
    ax1.set_xlim(min(times, default=0), max(times, default=0))

    return temp_lines + [line_valve, hline]

# === Run live animation ===
ani = FuncAnimation(
    fig, update, init_func=init,
    interval=1000, blit=True, cache_frame_data=False
)

plt.show()
ser.close()
