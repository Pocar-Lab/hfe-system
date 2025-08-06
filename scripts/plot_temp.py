#!/usr/bin/env python3
import time
from collections import deque
import serial
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
matplotlib.use('TkAgg')

# —————— Serial setup ——————
try:
    ser = serial.Serial('/dev/ttyACM0', 115200, timeout=0.1)
except serial.SerialException as e:
    print("ERROR opening /dev/ttyACM0:", e)
    exit(1)

time.sleep(2)
ser.reset_input_buffer()

# —————— Data buffers ——————
# 600 points max → 10 min at 1 Hz
times = deque(maxlen=600)
temps = deque(maxlen=600)

start_time = time.time()

# —————— Plot setup ——————
fig, ax = plt.subplots()
line, = ax.plot([], [], label='Temp (°C)', lw=2)
hline = ax.axhline(25.0, linestyle='--', label='Set-point (25 °C)')

ax.set_xlabel('Time (s)')
ax.set_ylabel('Temperature (°C)')
ax.legend(loc='upper left')

# Optionally fix to 0–600s view, or enable autoscale:
ax.set_xlim(0, 600)
ax.set_ylim(0, 50)

# —————— Animation callbacks ——————
def init():
    line.set_data([], [])
    return line, hline

def update(frame):
    latest = None
    # drain all available lines; keep only the last
    while True:
        raw = ser.readline().decode('utf-8', errors='ignore').strip()
        if not raw:
            break
        latest = raw

    if not latest or ',' not in latest:
        return line, hline

    parts = latest.split(',')
    if len(parts) < 2:
        return line, hline

    try:
        t_s   = float(parts[0])  # Arduino millis()/1000
        temp  = float(parts[1])
        # valve = int(parts[2])  # if you ever want to use it
    except ValueError:
        return line, hline

    # Record the very first Arduino timestamp and shift
    if not hasattr(update, 't0'):
        update.t0 = t_s
    times.append(t_s - update.t0)
    temps.append(temp)

    # update plot data
    line.set_data(times, temps)

    # (optional) scrolling X—uncomment if you prefer dynamic window:
    # ax.set_xlim(max(0, times[-1] - 600), times[-1] + 1)

    return line, hline

# —————— Run it at 1 Hz with blitting ——————
ani = FuncAnimation(
    fig, update,
    init_func=init,
    interval=1000,         # 1 s
    blit=True,
    cache_frame_data=False
)

plt.show()
