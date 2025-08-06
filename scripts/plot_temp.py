#!/usr/bin/env python3
"""
Live plotter for Arduino Mega2560 + MAX6675 + Valve control.

– Reads CSV lines “time_s,temp_C,valve” @ 115200 baud
– Plots temperature vs. time (0–50 °C, dynamic time window)
– Overlays valve state (0/1) on a second y-axis
– Draws a red dashed set-point at 25 °C
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
time.sleep(2)            # allow Arduino to reset
ser.reset_input_buffer()

# === Data buffers (10 min max at 1 Hz) ===
times  = deque(maxlen=600)
temps  = deque(maxlen=600)
valves = deque(maxlen=600)

# === Plot setup ===
fig, ax1 = plt.subplots()
ax2 = ax1.twinx()

line_temp,  = ax1.plot([], [], 'b-', label='Temp (°C)', lw=2)
line_valve, = ax2.step([], [], 'g-', label='Valve (0/1)', where='post')
hline = ax1.axhline(25.0, color='red', linestyle='--', label='Set-point (25 °C)')

ax1.set_xlabel('Time (s)')
ax1.set_ylabel('Temperature (°C)')
ax2.set_ylabel('Valve State')

ax1.set_ylim(0, 50)
ax2.set_ylim(-0.1, 1.1)

ax1.legend(loc='upper left')
ax2.legend(loc='upper right')

def init():
    line_temp.set_data([], [])
    line_valve.set_data([], [])
    return line_temp, line_valve, hline

def update(frame):
    latest = None
    # Drain buffer, keep only the last line
    while ser.in_waiting:
        raw = ser.readline().decode('utf-8', errors='ignore').strip()
        if raw:
            latest = raw

    if not latest:
        return line_temp, line_valve, hline

    parts = latest.split(',')
    if len(parts) != 3:
        return line_temp, line_valve, hline

    try:
        t    = float(parts[0])
        temp = float(parts[1])
        valve_state = int(parts[2])
    except ValueError:
        return line_temp, line_valve, hline

    # Append to buffers
    times.append(t)
    temps.append(temp)
    valves.append(valve_state)

    # Update line data
    line_temp.set_data(times, temps)
    line_valve.set_data(times, valves)

    # Auto‐scale X axis to show all collected data
    ax1.set_xlim(min(times, default=0), max(times, default=0))

    return line_temp, line_valve, hline

# === Run animation at 1 Hz ===
ani = FuncAnimation(
    fig, update, init_func=init,
    interval=1000, blit=True, cache_frame_data=False
)

plt.show()
ser.close()
