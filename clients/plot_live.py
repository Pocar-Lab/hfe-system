#!/usr/bin/env python3
"""
TC monitor + valve UI driven by the supervisor WebSocket stream.

Matches the layout and behaviour of scripts/plot_temp.py but consumes
telemetry from ws://<HOST>/ws instead of a serial CSV feed.
"""
from __future__ import annotations

import asyncio
import csv
import json
import math
import os
import queue
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, List, Optional, TextIO, Tuple

try:
    import requests
except Exception:  # pragma: no cover - optional dependency
    requests = None

# reorganize imports to include helper from urllib
import tkinter as tk
from tkinter import filedialog, ttk

import matplotlib

matplotlib.use("TkAgg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.animation import FuncAnimation  # noqa: E402
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # noqa: E402

import websockets  # noqa: E402
from websockets.exceptions import ConnectionClosed  # noqa: E402


def _normalize_host(raw_host: str | None) -> str:
    host = (raw_host or "").strip()
    if not host:
        return "127.0.0.1:8000"
    for prefix in ("ws://", "wss://", "http://", "https://"):
        if host.startswith(prefix):
            host = host[len(prefix) :]
            break
    if "/" in host:
        host = host.split("/", 1)[0]
    if host.startswith("["):
        # IPv6 literal, ensure trailing :port
        if "]" in host:
            closing_idx = host.index("]")
            suffix = host[closing_idx + 1 :]
            if not suffix.startswith(":"):
                host = f"{host}:8000"
    elif ":" not in host:
        host = f"{host}:8000"
    return host


# ── Config ────────────────────────────────────────────────────────────────
HOST = _normalize_host(os.environ.get("SUP_HOST"))
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "").strip()
WS_URL = f"ws://{HOST}/ws"
if TOKEN:
    WS_URL += f"?token={TOKEN}"

API_BASE = os.environ.get("SUP_API", f"http://{HOST}")
AUTH_HEADER = (
    TOKEN
    if TOKEN.lower().startswith("bearer ")
    else (f"Bearer {TOKEN}" if TOKEN else "")
)
HEADERS = {"Authorization": AUTH_HEADER} if AUTH_HEADER else {}

MAX_SENSORS: int = 10          # U0..U9
SETPOINT_C: float = 25.0

X_MAX_MIN: float = 15.0        # fixed 0..15 minutes
WINDOW_LEN_S: int = int(X_MAX_MIN * 60.0)  # 900 samples @ 1 Hz
ANIM_INTERVAL_MS: int = 1000

DETECT_ZERO_AS_NC: bool = True
VALID_RANGE_C: Tuple[float, float] = (-200.0, 1800.0)

# ───────────────────────────────────────────────────────────────────────────


class WSMonitorApp:
    """GUI + WebSocket + plotting + CSV logging."""

    def __init__(self) -> None:
        self.running: bool = True

        # Data buffers
        self.times: Deque[float] = deque(maxlen=WINDOW_LEN_S)  # minutes
        self.temps: List[Deque[float]] = [deque(maxlen=WINDOW_LEN_S) for _ in range(MAX_SENSORS)]
        self.start_time: Optional[float] = None

        # Sensor count (detected from payload)
        self.nsensors: int = MAX_SENSORS
        self.legend_nsensors: int = -1

        # Logging
        self.log_enabled: bool = False
        self.log_path: Optional[Path] = None
        self.log_fh: Optional[TextIO] = None
        self.log_writer: Optional[Any] = None

        # GUI
        self.root = tk.Tk()
        self.root.geometry("1200x700")
        self.root.title("TC Monitor (WebSocket)")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        self.figure, self.axes = plt.subplots()
        self.lines: List = []
        for i in range(MAX_SENSORS):
            (line_obj,) = self.axes.plot([], [], "-", lw=2, label=f"U{i}")
            line_obj.set_alpha(0.25)
            self.lines.append(line_obj)

        self.hline = self.axes.axhline(SETPOINT_C, linestyle="--", label=f"Set-point ({SETPOINT_C} °C)")
        self.axes.set_xlabel("Time (min)")
        self.axes.set_ylabel("Temperature (°C)")
        self.axes.set_ylim(-170, 25)
        self.axes.set_xlim(0, X_MAX_MIN)
        self._rebuild_legend(0)

        self.canvas = FigureCanvasTkAgg(self.figure, master=self.root)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        # Side panel
        side = ttk.Frame(self.root, padding=12)
        side.grid(row=0, column=1, sticky="ns")

        ttk.Label(side, text="Valve State", font=("TkDefaultFont", 12, "bold")).grid(row=0, column=0, pady=(0, 4), sticky="w")
        self.state_var = tk.StringVar(value="—")
        self.state_lbl = tk.Label(side, textvariable=self.state_var, width=12, relief="groove", font=("TkDefaultFont", 18, "bold"))
        self.state_lbl.grid(row=1, column=0, pady=(0, 12), sticky="we")

        self.mode_var = tk.StringVar(value="Mode: —")
        ttk.Label(side, textvariable=self.mode_var).grid(row=2, column=0, pady=(0, 8), sticky="w")

        self.avg_var = tk.StringVar(value="Avg (valid): —")
        ttk.Label(side, textvariable=self.avg_var).grid(row=3, column=0, pady=(0, 4), sticky="w")

        self.count_var = tk.StringVar(value="Sensors: —")
        ttk.Label(side, textvariable=self.count_var).grid(row=4, column=0, pady=(0, 12), sticky="w")

        self.status_var = tk.StringVar(value="Status: starting…")
        ttk.Label(side, textvariable=self.status_var).grid(row=5, column=0, sticky="w", pady=(0, 12))

        btn_frame = ttk.LabelFrame(side, text="Override")
        btn_frame.grid(row=6, column=0, sticky="we", pady=(0, 12))
        btn_frame.columnconfigure(0, weight=1)
        ttk.Button(btn_frame, text="OPEN", command=lambda: self.send_cmd("VALVE OPEN")).grid(row=0, column=0, sticky="we", pady=2)
        ttk.Button(btn_frame, text="CLOSE", command=lambda: self.send_cmd("VALVE CLOSE")).grid(row=1, column=0, sticky="we", pady=2)
        ttk.Button(btn_frame, text="AUTO", command=lambda: self.send_cmd("VALVE AUTO")).grid(row=2, column=0, sticky="we", pady=2)

        # Logging controls
        self.log_var = tk.StringVar(value="Logging: off")
        ttk.Label(side, textvariable=self.log_var).grid(row=7, column=0, sticky="w", pady=(0, 6))
        self.log_btn = ttk.Button(side, text="Start Logging", command=self.start_logging)
        self.log_btn.grid(row=8, column=0, sticky="we", pady=(0, 8))

        ttk.Button(side, text="Reset Plot", command=self.reset_plot).grid(row=9, column=0, sticky="we", pady=(0, 8))
        ttk.Button(side, text="Quit", command=self.on_close).grid(row=10, column=0, sticky="we")

        # Queue + worker
        self.msg_queue: queue.Queue[Tuple[float, List[float], int, str, int]] = queue.Queue(maxsize=WINDOW_LEN_S * 2)
        self.ws_thread = threading.Thread(target=self._run_ws_client, daemon=True)
        self.ws_thread.start()

        self.anim = FuncAnimation(
            self.figure,
            self.update,
            init_func=self.init_anim,
            interval=ANIM_INTERVAL_MS,
            blit=False,
            cache_frame_data=False,
        )

    # ── Logging helpers ────────────────────────────────────────────────────
    def start_logging(self) -> None:
        default_name = f"tc_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path_str = filedialog.asksaveasfilename(
            title="Save CSV log",
            initialfile=default_name,
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path_str:
            return
        self.log_path = Path(path_str)
        try:
            self.log_fh = self.log_path.open("w", newline="", encoding="utf-8")
        except OSError as exc:
            self.log_var.set(f"Log error: {exc}")
            return
        self.log_writer = csv.writer(self.log_fh)
        header = ["time_s"] + [f"temp{i}_C" for i in range(MAX_SENSORS)] + ["valve", "mode"]
        self.log_writer.writerow(header)
        self.log_fh.flush()
        self.log_enabled = True
        self.log_var.set(f"Logging to: {self.log_path.name}")
        if self.log_btn and self.log_btn.winfo_exists():
            self.log_btn.configure(text="Stop Logging", command=self.stop_logging)

    def stop_logging(self, *, ui_update: bool = True) -> None:
        """Stop CSV logging. If ui_update=False, do not touch Tk widgets."""
        if self.log_enabled:
            try:
                if self.log_fh:
                    self.log_fh.flush()
                    self.log_fh.close()
            except OSError:
                pass
        self.log_enabled = False
        self.log_fh = None
        self.log_writer = None

        if ui_update and self.root and self.root.winfo_exists():
            try:
                if self.log_var:
                    self.log_var.set("Logging: off")
                if self.log_btn and self.log_btn.winfo_exists():
                    self.log_btn.configure(text="Start Logging", command=self.start_logging)
            except tk.TclError:
                pass

    def _log_row(self, t_sec: float, vals: List[float], valve: int, mode_char: str) -> None:
        if not (self.log_enabled and self.log_writer):
            return
        row: List[str] = [f"{t_sec:.3f}"]
        for i in range(MAX_SENSORS):
            v = vals[i] if i < len(vals) else math.nan
            row.append(f"{v:.2f}" if isinstance(v, float) and math.isfinite(v) else "nan")
        row.extend([str(int(valve)), mode_char[:1]])
        self.log_writer.writerow(row)
        if self.log_fh:
            self.log_fh.flush()

    # ── GUI helpers ───────────────────────────────────────────────────────
    def _rebuild_legend(self, active: int) -> None:
        handles = [self.lines[i] for i in range(active)] + [self.hline]
        labels = [f"U{i}" for i in range(active)] + [f"Set-point ({SETPOINT_C} °C)"]

        self.axes.legend(
            handles,
            labels,
            loc="upper left",
            bbox_to_anchor=(1.02, 1.0),
            borderaxespad=0,
            frameon=True,
            ncol=1,
        )
        self.figure.tight_layout(rect=[0, 0, 0.85, 1])

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            self.cleanup()

    def send_cmd(self, cmd: str) -> None:
        if not requests:
            print(f"[WARN] requests module unavailable; cannot send command '{cmd}'")
            return
        payload = {"cmd": cmd}
        try:
            response = requests.post(f"{API_BASE}/api/command", json=payload, headers=HEADERS, timeout=5)
            response.raise_for_status()
        except Exception as exc:
            self.state_lbl.configure(bg="light gray")
            print(f"[WARN] Command send failed: {exc}")

    def reset_plot(self) -> None:
        self.start_time = None
        self.times.clear()
        for dq in self.temps:
            dq.clear()
        for line_obj in self.lines:
            line_obj.set_data([], [])
            line_obj.set_alpha(0.25)
        self.axes.set_xlim(0, X_MAX_MIN)
        self.canvas.draw_idle()

    def on_close(self) -> None:
        """Stop animation, stop logging (with UI updates), close worker, destroy Tk."""
        if not self.running:
            return
        self.running = False
        try:
            if self.anim and self.anim.event_source:
                self.anim.event_source.stop()
        except Exception:
            pass
        self.stop_logging(ui_update=True)
        self.cleanup()
        try:
            self.root.quit()
        finally:
            self.root.destroy()

    # ── Animation ─────────────────────────────────────────────────────────
    def init_anim(self):
        for line_obj in self.lines:
            line_obj.set_data([], [])
            line_obj.set_alpha(0.25)
        self.axes.set_xlim(0, X_MAX_MIN)
        return (*self.lines, self.hline)

    def update(self, _frame):
        if not self.running:
            return (*self.lines, self.hline)

        latest: Optional[Tuple[float, List[float], int, str, int]] = None
        while True:
            try:
                latest = self.msg_queue.get_nowait()
            except queue.Empty:
                break

        if latest is None:
            return (*self.lines, self.hline)

        t_sec, vals, valve, mode_char, sensor_count = latest
        self.nsensors = max(1, min(sensor_count, MAX_SENSORS))

        if self.legend_nsensors != self.nsensors:
            self._rebuild_legend(self.nsensors)
            self.legend_nsensors = self.nsensors

        if self.start_time is None:
            self.start_time = t_sec

        t_min = (t_sec - self.start_time) / 60.0
        self.times.append(t_min)
        for i in range(MAX_SENSORS):
            v = vals[i] if i < len(vals) else math.nan
            self.temps[i].append(v)

        for i, line_obj in enumerate(self.lines):
            line_obj.set_data(self.times, self.temps[i])
            last_v = self.temps[i][-1] if self.temps[i] else math.nan
            is_active = (i < self.nsensors) and isinstance(last_v, float) and math.isfinite(last_v)
            line_obj.set_alpha(1.0 if is_active else (0.35 if i < self.nsensors else 0.0))

        self.axes.set_xlim(0, X_MAX_MIN)

        valid_vals = [v for v in vals[:self.nsensors] if isinstance(v, float) and math.isfinite(v)]
        k_valid = len(valid_vals)
        if k_valid:
            self.avg_var.set(f"Avg (valid {k_valid}): {sum(valid_vals) / k_valid:.2f} °C")
        else:
            self.avg_var.set("Avg (valid 0): —")
        self.count_var.set(f"Sensors active: {self.nsensors}  •  Valid now: {k_valid}")

        valve_state = bool(valve)
        self.state_var.set("OPEN" if valve_state else "CLOSED")
        self.state_lbl.configure(bg=("green" if valve_state else "light gray"))
        mode_text = {"A": "AUTO", "O": "FORCED OPEN", "C": "FORCED CLOSE"}.get(mode_char.upper(), "AUTO")
        self.mode_var.set(f"Mode: {mode_text}")

        self._log_row(t_sec, vals, valve, mode_char)
        self.canvas.draw_idle()
        return (*self.lines, self.hline)

    # ── WebSocket helpers ─────────────────────────────────────────────────
    def _run_ws_client(self) -> None:
        self._set_status(f"Status: connecting to {WS_URL}")
        try:
            asyncio.run(self._ws_loop())
        except Exception as exc:
            self._set_status(f"Status: worker stopped ({exc})")
            print(f"[WARN] WebSocket worker exited: {exc}")

    async def _ws_loop(self) -> None:
        backoff = 1.0
        while self.running:
            try:
                async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
                    self._set_status("Status: telemetry connected")
                    backoff = 1.0
                    while self.running:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        except ConnectionClosed:
                            break
                        except Exception as exc:
                            self._set_status(f"Status: recv error ({exc}); reconnecting")
                            break
                        payload = self._normalize_payload(msg)
                        if payload:
                            self._enqueue_payload(payload)
                            self._set_status("Status: receiving telemetry")
                if not self.running:
                    break
            except Exception as exc:
                print(f"[WARN] WebSocket connection error: {exc}")
                if not self.running:
                    break
                wait = backoff
                self._set_status(f"Status: reconnecting in {wait:.1f}s ({exc})")
                await asyncio.sleep(wait)
                backoff = min(backoff * 2.0, 30.0)

    def _enqueue_payload(self, payload: Tuple[float, List[float], int, str, int]) -> None:
        while self.running:
            try:
                self.msg_queue.put_nowait(payload)
                break
            except queue.Full:
                try:
                    self.msg_queue.get_nowait()
                except queue.Empty:
                    pass

    def _normalize_payload(self, raw: str) -> Optional[Tuple[float, List[float], int, str, int]]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if data.get("type") != "telemetry":
            return None

        temps_raw = data.get("temps")
        if isinstance(temps_raw, list) and temps_raw:
            sensor_count = min(len(temps_raw), MAX_SENSORS)
            temps = [self._sanitize_temp(val) for val in temps_raw[:MAX_SENSORS]]
        else:
            alt = data.get("tC")
            if alt is None:
                return None
            temps = [self._sanitize_temp(alt)]
            sensor_count = 1

        temps.extend([math.nan] * (MAX_SENSORS - len(temps)))

        t_raw = data.get("t")
        try:
            t_sec = float(t_raw) if t_raw is not None else time.time()
        except (TypeError, ValueError):
            t_sec = time.time()

        valve_raw = data.get("valve")
        try:
            valve = int(valve_raw)
        except (TypeError, ValueError):
            valve = 0

        mode_raw = data.get("mode", "A")
        mode_char = str(mode_raw)[:1].upper() if mode_raw else "A"

        return t_sec, temps, valve, mode_char, sensor_count

    # ── Parsing helpers ───────────────────────────────────────────────────
    @staticmethod
    def _sanitize_temp(val_raw) -> float:
        text = str(val_raw).strip().lower()
        if text in {"", "nan"}:
            return math.nan
        try:
            value = float(text)
        except ValueError:
            return math.nan
        if DETECT_ZERO_AS_NC and abs(value) < 1e-12:
            return math.nan
        if not (VALID_RANGE_C[0] <= value <= VALID_RANGE_C[1]):
            return math.nan
        return value

    # ── Cleanup ───────────────────────────────────────────────────────────
    def cleanup(self) -> None:
        self.running = False
        try:
            if self.anim and self.anim.event_source:
                self.anim.event_source.stop()
        except Exception:
            pass
        self.stop_logging(ui_update=False)
        try:
            plt.close(self.figure)
        except Exception:
            pass
        if self.ws_thread and self.ws_thread.is_alive():
            self.ws_thread.join(timeout=2.0)
        self._set_status("Status: stopped")

    def _set_status(self, text: str) -> None:
        if not self.root:
            return
        try:
            if not self.root.winfo_exists():
                return
        except tk.TclError:
            return
        try:
            self.root.after(0, lambda: self.status_var.set(text))
        except tk.TclError:
            pass


def main() -> None:
    app = WSMonitorApp()
    app.run()


if __name__ == "__main__":
    main()
