#!/usr/bin/env python3
"""
Live plotter for Arduino Mega2560 + 2x MAX31856 + Valve.

Reads CSV from serial (printed by Arduino sketch):
  time_s,temp1_C,temp2_C,valve

Features:
  - Plots Temp1, Temp2, and their AVERAGE
  - Overlays valve state (0/1) on a second y-axis
  - Shows a horizontal set-point line
Usage:
  python live_plot.py /dev/ttyACM0
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
SETPOINT_C = 23.0
MAXLEN = 600  # ~10 min at 1 Hz

# === Serial ===
ser = serial.Serial(PORT, BAUD, timeout=0.1)
time.sleep(2)            # let Arduino reset
ser.reset_input_buffer()

# === Buffers ===
times = deque(maxlen=MAXLEN)
t1buf = deque(maxlen=MAXLEN)
t2buf = deque(maxlen=MAXLEN)
tavg  = deque(maxlen=MAXLEN)
vbuf  = deque(maxlen=MAXLEN)

# === Plot ===
fig, ax1 = plt.subplots()
ax2 = ax1.twinx()

line_t1,   = ax1.plot([], [], lw=2, label='Temp1 (°C)')
line_t2,   = ax1.plot([], [], lw=2, label='Temp2 (°C)')
line_tavg, = ax1.plot([], [], lw=2, linestyle='--', label='Average (°C)')
line_valve, = ax2.step([], [], where='post', label='Valve (0/1)')

hline = ax1.axhline(SETPOINT_C, linestyle='--', label=f'Set-point ({SETPOINT_C:g} °C)')

ax1.set_xlabel('Time (s)')
ax1.set_ylabel('Temperature (°C)')
ax2.set_ylabel('Valve State')

ax1.set_ylim(min(-200.0, SETPOINT_C - 5), max(25.0, SETPOINT_C + 5))
ax2.set_ylim(-0.1, 1.1)

ax1.legend(loc='upper left')
ax2.legend(loc='upper right')

def _parse_row(raw: str):
    parts = raw.strip().split(',')
    if len(parts) != 4:
        return None
    try:
        t = float(parts[0])
        t1 = float(parts[1])
        t2 = float(parts[2])
        v  = int(parts[3])
    except ValueError:
        return None
    return t, t1, t2, v

def init():
    line_t1.set_data([], [])
    line_t2.set_data([], [])
    line_tavg.set_data([], [])
    line_valve.set_data([], [])
    return line_t1, line_t2, line_tavg, line_valve, hline

def update(_frame):
    latest = None
    # Drain all currently waiting lines, keep last good row
    while ser.in_waiting:
        raw = ser.readline().decode('utf-8', errors='ignore').strip()
        if not raw:
            continue
        if raw.lower().startswith('time'):
            continue
        parsed = _parse_row(raw)
        if parsed:
            latest = parsed

    if latest is None:
        return line_t1, line_t2, line_tavg, line_valve, hline

    t, t1, t2, v = latest
    times.append(t)
    t1buf.append(t1)
    t2buf.append(t2)

    # Average (handle NaNs printed as 'nan')
    a_ok = (t1 == t1)
    b_ok = (t2 == t2)
    if a_ok and b_ok:
        tavg.append(0.5 * (t1 + t2))
    elif a_ok:
        tavg.append(t1)
    elif b_ok:
        tavg.append(t2)
    else:
        tavg.append(float('nan'))

    vbuf.append(v)

    # Update lines
    line_t1.set_data(times, t1buf)
    line_t2.set_data(times, t2buf)
    line_tavg.set_data(times, tavg)
    line_valve.set_data(times, vbuf)

    if times:
        ax1.set_xlim(times[0], times[-1])

    return line_t1, line_t2, line_tavg, line_valve, hline

ani = FuncAnimation(fig, update, init_func=init, interval=1000, blit=True, cache_frame_data=False)

try:
    plt.show()
finally:
    ser.close()
