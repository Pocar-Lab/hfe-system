from __future__ import annotations

import asyncio
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

import requests
import tkinter as tk
from tkinter import ttk
import websockets

HOST = os.environ.get("SUP_HOST", "127.0.0.1:8000")
API_BASE = os.environ.get("SUP_API", f"http://{HOST}")
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "").strip()
AUTH_HEADER = TOKEN if TOKEN.lower().startswith("bearer ") else (f"Bearer {TOKEN}" if TOKEN else "")
HEADERS = {"Authorization": AUTH_HEADER} if AUTH_HEADER else {}
WS_URL = f"ws://{HOST}/ws"
if TOKEN:
    WS_URL += f"?token={TOKEN}"


def post_command(payload: dict[str, Any]) -> None:
    response = requests.post(f"{API_BASE}/api/command", json=payload, headers=HEADERS, timeout=5)
    response.raise_for_status()


def start_telemetry_consumer(on_update: Callable[[dict[str, Any]], None], on_status: Callable[[str], None]) -> None:
    async def consume() -> None:
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
                    on_status("Telemetry: connected")
                    backoff = 1.0
                    async for message in ws:
                        try:
                            payload = json.loads(message)
                        except json.JSONDecodeError:
                            continue
                        if payload.get("type") == "telemetry":
                            on_update(payload)
                on_status("Telemetry: disconnected")
            except Exception as exc:
                on_status(f"Telemetry reconnecting in {backoff:.0f}s: {exc}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    threading.Thread(target=lambda: asyncio.run(consume()), daemon=True).start()


@dataclass
class UI:
    root: tk.Tk
    dispatcher: ThreadPoolExecutor
    status_var: tk.StringVar
    readout_var: tk.StringVar


def build_ui() -> UI:
    root = tk.Tk()
    root.title("Cryo SlowCtrl")
    root.geometry("360x260")

    status_var = tk.StringVar(value="Ready")
    readout_var = tk.StringVar(value="T=— °C | valve=—")

    dispatcher = ThreadPoolExecutor(max_workers=3)

    frame = ttk.Frame(root, padding=12)
    frame.grid(row=0, column=0, sticky="nsew")
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)

    ttk.Label(frame, text="Setpoint (°C)").grid(row=0, column=0, sticky="e")
    entry_sp = ttk.Entry(frame)
    entry_sp.insert(0, "25.0")
    entry_sp.grid(row=0, column=1, sticky="we")

    ttk.Label(frame, text="Hysteresis (°C)").grid(row=1, column=0, sticky="e")
    entry_hy = ttk.Entry(frame)
    entry_hy.insert(0, "0.5")
    entry_hy.grid(row=1, column=1, sticky="we")

    ttk.Label(frame, text="Telemetry (ms)").grid(row=2, column=0, sticky="e")
    entry_tm = ttk.Entry(frame)
    entry_tm.insert(0, "1000")
    entry_tm.grid(row=2, column=1, sticky="we")

    def submit(payload: dict[str, Any]) -> None:
        status_var.set("Sending command…")

        def task() -> None:
            try:
                post_command(payload)
            except Exception as exc:
                root.after(0, lambda: status_var.set(f"Command failed: {exc}"))
            else:
                root.after(0, lambda: status_var.set("Command sent"))

        dispatcher.submit(task)

    def apply_settings() -> None:
        try:
            payload = {
                "id": "set_control",
                "setpoint_C": float(entry_sp.get()),
                "hysteresis_C": float(entry_hy.get()),
                "telemetry_ms": int(entry_tm.get()),
            }
        except ValueError:
            status_var.set("Invalid numeric input")
            return
        submit(payload)

    ttk.Button(frame, text="Apply", command=apply_settings).grid(row=3, column=0, columnspan=2, sticky="we", pady=6)
    ttk.Button(frame, text="Open Valve", command=lambda: submit({"id": "set_valve", "open": 1})).grid(row=4, column=0, sticky="we")
    ttk.Button(frame, text="Close Valve", command=lambda: submit({"id": "set_valve", "open": 0})).grid(row=4, column=1, sticky="we")

    ttk.Label(frame, textvariable=readout_var, font=("TkDefaultFont", 12, "bold")).grid(row=5, column=0, columnspan=2, pady=(12, 4))
    ttk.Label(frame, textvariable=status_var).grid(row=6, column=0, columnspan=2, sticky="w")

    for col in range(2):
        frame.columnconfigure(col, weight=1)

    def on_close() -> None:
        dispatcher.shutdown(wait=False, cancel_futures=True)
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    return UI(root=root, dispatcher=dispatcher, status_var=status_var, readout_var=readout_var)


def main() -> None:
    ui = build_ui()

    def handle_update(payload: dict[str, Any]) -> None:
        try:
            temp = float(payload.get("tC"))
            temp_text = f"T={temp:.2f} °C"
        except (TypeError, ValueError):
            temp_text = "T=— °C"

        valve_state = payload.get("valve")
        valve_text = "valve=OPEN" if valve_state else "valve=CLOSED"
        ui.root.after(0, lambda: ui.readout_var.set(f"{temp_text} | {valve_text}"))

    def handle_status(message: str) -> None:
        ui.root.after(0, lambda: ui.status_var.set(message))

    start_telemetry_consumer(handle_update, handle_status)
    ui.root.mainloop()


if __name__ == "__main__":
    main()
