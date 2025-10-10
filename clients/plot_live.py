#!/usr/bin/env python3
"""
Live plotter for the supervisor WebSocket stream.

- Connects to ws://<HOST>/ws?token=<TOKEN> (token from SUPERVISOR_TOKEN env var)
- Plots temperature vs. time and valve state (0/1)
- Requires: websockets, matplotlib
"""

import os
import asyncio
import json
import time
from collections import deque

import websockets
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt  # noqa: E402

# ── Config: pick host and token from env, with sensible defaults ─────────
HOST = os.environ.get("SUP_HOST", "127.0.0.1:8000")  # set to "<LAB_VPN_IP>:8000" when remote
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
WS_URL = f"ws://{HOST}/ws"
if TOKEN:
    WS_URL += f"?token={TOKEN}"

# ── Data buffers ──────────────────────────────────────────────────────────
times = deque(maxlen=600)    # 10 minutes @ 1 Hz if firmware streams 1 Hz
temps = deque(maxlen=600)
valves = deque(maxlen=600)

# ── Plot setup ────────────────────────────────────────────────────────────
fig, ax1 = plt.subplots()
ax2 = ax1.twinx()

(line_temp,) = ax1.plot([], [], lw=2, label="Temp (°C)")
(line_valve,) = ax2.step([], [], where="post", label="Valve (0/1)")
hline = ax1.axhline(25.0, linestyle="--", label="Setpoint")

ax1.set_xlabel("Time (s)")
ax1.set_ylabel("Temperature (°C)")
ax2.set_ylabel("Valve")
ax2.set_ylim(-0.1, 1.1)
ax1.legend(loc="upper left")
ax2.legend(loc="upper right")

def redraw():
    if not times:
        return
    line_temp.set_data(times, temps)
    line_valve.set_data(times, valves)
    ax1.set_xlim(min(times), max(times))
    ax1.relim()
    ax1.autoscale_view(True, True, False)
    fig.canvas.draw_idle()
    fig.canvas.flush_events()

# ── WebSocket consumer ────────────────────────────────────────────────────
async def main():
    print(f"Connecting to {WS_URL}")
    # The server expects no special headers; token goes in the query string
    async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
        while True:
            msg = await ws.recv()
            try:
                obj = json.loads(msg)
            except json.JSONDecodeError:
                continue

            if obj.get("type") != "telemetry":
                continue

            # Extract fields with fallbacks
            t_raw = obj.get("t", time.time())
            t = float(t_raw)
            tC = obj.get("tC", None)
            valve = obj.get("valve", None)

            # Only plot if we have the essentials
            if tC is None or valve is None:
                continue

            times.append(t)
            temps.append(float(tC))
            valves.append(int(valve))

            redraw()

if __name__ == "__main__":
    plt.ion()
    try:
        asyncio.run(main())
    finally:
        plt.ioff()
        plt.show()
