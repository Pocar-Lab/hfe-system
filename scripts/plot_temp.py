#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TC monitor + valve UI for Mega2560 + up to 10× MAX31856, with CSV logging.

CSV stream from Arduino (1 Hz):
  time_s, temp0_C, ... temp9_C, valve, mode
"""

from __future__ import annotations

import csv
import math
import time
from datetime import datetime
from pathlib import Path
from collections import deque
from typing import Deque, List, Optional, Tuple, TextIO

import tkinter as tk
from tkinter import ttk, filedialog

import serial
from serial import SerialException

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# ── Config ────────────────────────────────────────────────────────────────
PORT: str = "/dev/ttyACM0"   # change if needed (e.g. 'COM5' on Windows)
BAUD: int = 115200

MAX_SENSORS: int = 10          # U0..U9
SETPOINT_C: float = 25.0

X_MAX_MIN: float = 15.0        # fixed 0..15 minutes
WINDOW_LEN_S: int = int(X_MAX_MIN * 60.0)  # 900 samples @ 1 Hz
ANIM_INTERVAL_MS: int = 1000

DETECT_ZERO_AS_NC: bool = True
VALID_RANGE_C: Tuple[float, float] = (-200.0, 1800.0)


class TCMonitorApp:
    """GUI + serial + plotting + CSV logging."""

    def __init__(self, port: str = PORT, baud: int = BAUD) -> None:
        # Serial
        try:
            self.serial = serial.Serial(port, baud, timeout=0.1)
        except SerialException as exc:
            raise SystemExit(f"Failed to open serial port {port}: {exc}") from exc
        time.sleep(2.0)
        self.serial.reset_input_buffer()

        # Data buffers
        self.times: Deque[float] = deque(maxlen=WINDOW_LEN_S)  # minutes
        self.temps: List[Deque[float]] = [deque(maxlen=WINDOW_LEN_S) for _ in range(MAX_SENSORS)]
        self.start_time: Optional[float] = None
        self.running: bool = True

        # Sensor count (detected from header/line length)
        self.nsensors: int = MAX_SENSORS
        self.legend_nsensors: int = -1

        # Logging
        self.log_enabled: bool = False
        self.log_path: Optional[Path] = None
        self.log_fh: Optional[TextIO] = None
        self.log_writer: Optional[csv.writer] = None

        # GUI
        self.root = tk.Tk()
        self.root.geometry("1200x700")
        self.root.title("TC Monitor (MAX31856) + Valve Control")
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

        btn_frame = ttk.LabelFrame(side, text="Override")
        btn_frame.grid(row=5, column=0, sticky="we", pady=(0, 12))
        btn_frame.columnconfigure(0, weight=1)
        ttk.Button(btn_frame, text="OPEN",  command=lambda: self.send_cmd("VALVE OPEN")).grid(row=0, column=0, sticky="we", pady=2)
        ttk.Button(btn_frame, text="CLOSE", command=lambda: self.send_cmd("VALVE CLOSE")).grid(row=1, column=0, sticky="we", pady=2)
        ttk.Button(btn_frame, text="AUTO",  command=lambda: self.send_cmd("VALVE AUTO")).grid(row=2, column=0, sticky="we", pady=2)

        # Logging controls
        self.log_var = tk.StringVar(value="Logging: off")
        ttk.Label(side, textvariable=self.log_var).grid(row=6, column=0, sticky="w", pady=(0, 6))
        self.log_btn = ttk.Button(side, text="Start Logging", command=self.start_logging)
        self.log_btn.grid(row=7, column=0, sticky="we", pady=(0, 8))

        ttk.Button(side, text="Reset Plot", command=self.reset_plot).grid(row=8, column=0, sticky="we", pady=(0, 8))
        ttk.Button(side, text="Quit", command=self.on_close).grid(row=9, column=0, sticky="we")

        self.anim = FuncAnimation(self.figure, self.update, init_func=self.init_anim,
                                  interval=ANIM_INTERVAL_MS, blit=False, cache_frame_data=False)

    # ── Logging helpers ────────────────────────────────────────────────────
    def start_logging(self) -> None:
        default_name = f"tc_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path_str = filedialog.asksaveasfilename(
            title="Save CSV log",
            initialfile=default_name,
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
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
        # Only reconfigure the button if it still exists
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
                # Widget was destroyed between existence check and configure; ignore
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
            bbox_to_anchor=(1.02, 1.0),  # Outside top-right
            borderaxespad=0,
            frameon=True,
            ncol=1
        )
        self.figure.tight_layout(rect=[0, 0, 0.85, 1])  # Shrink plot to leave space on right


    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            self.cleanup()

    def send_cmd(self, cmd: str) -> None:
        try:
            self.serial.write((cmd + "\n").encode("ascii"))
        except (SerialException, OSError) as exc:
            self.state_lbl.configure(bg="light gray")
            print(f"[WARN] Serial write failed: {exc}")

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
        """Stop animation, stop logging (with UI updates), close serial, destroy Tk."""
        if not self.running:
            return
        self.running = False
        # Stop the animation timer first
        try:
            if self.anim and self.anim.event_source:
                self.anim.event_source.stop()
        except Exception:
            pass
        # Stop logging while UI is still alive; guard UI updates inside
        self.stop_logging(ui_update=True)
        # Proceed with cleanup and destroy
        self.cleanup()
        try:
            self.root.quit()
        finally:
            # After this, Tk widgets are gone; don't touch them anymore
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

        latest = None

        # Drain serial; detect header; keep last complete line
        try:
            while self.serial.in_waiting:
                raw = self.serial.readline().decode("utf-8", errors="ignore").strip()
                if not raw:
                    continue
                if raw.startswith("time_s"):
                    parts = [p.strip() for p in raw.split(",")]
                    detected = sum(1 for p in parts if p.lower().startswith("temp"))
                    if 1 <= detected <= MAX_SENSORS:
                        self.nsensors = detected
                    continue
                if raw.startswith("#"):
                    continue
                parsed = self.parse_line_flexible(raw)
                if parsed:
                    latest = parsed
        except (SerialException, OSError) as exc:
            print(f"[WARN] Serial read failed: {exc}")
            return (*self.lines, self.hline)

        if latest is None:
            return (*self.lines, self.hline)

        t_sec, vals, valve, mode_char = latest

        # Legend update if sensor count changed
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

        # Update lines
        for i, line_obj in enumerate(self.lines):
            line_obj.set_data(self.times, self.temps[i])
            last_v = self.temps[i][-1] if self.temps[i] else math.nan
            is_active = (i < self.nsensors) and isinstance(last_v, float) and math.isfinite(last_v)
            line_obj.set_alpha(1.0 if is_active else (0.35 if i < self.nsensors else 0.0))

        # Fixed x-limits
        self.axes.set_xlim(0, X_MAX_MIN)

        # Right panel stats
        valid_vals = [v for v in vals[:self.nsensors] if isinstance(v, float) and math.isfinite(v)]
        k_valid = len(valid_vals)
        self.avg_var.set(f"Avg (valid {k_valid}): {sum(valid_vals) / k_valid:.2f} °C" if k_valid else "Avg (valid 0): —")
        self.count_var.set(f"Sensors active: {self.nsensors}  •  Valid now: {k_valid}")

        self.state_var.set("OPEN" if valve else "CLOSED")
        self.state_lbl.configure(bg=("green" if valve else "light gray"))
        mode_text = {"A": "AUTO", "O": "FORCED OPEN", "C": "FORCED CLOSE"}.get(mode_char.upper(), "AUTO")
        self.mode_var.set(f"Mode: {mode_text}")

        # Log the latest row (1 Hz)
        self._log_row(t_sec, vals, valve, mode_char)

        self.canvas.draw_idle()
        return (*self.lines, self.hline)

    # ── Parsing ───────────────────────────────────────────────────────────
    @staticmethod
    def _sanitize_temp(val_raw: str) -> float:
        text = val_raw.strip().lower()
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

    def parse_line_flexible(self, raw: str) -> Optional[Tuple[float, List[float], int, str]]:
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) < 3:
            return None
        try:
            t_sec = float(parts[0])
        except ValueError:
            return None
        try:
            valve = int(parts[-2])
            mode_char = parts[-1][:1]
        except (ValueError, IndexError):
            return None
        temp_fields = parts[1:-2]
        if not temp_fields:
            return None
        if 1 <= len(temp_fields) <= MAX_SENSORS:
            self.nsensors = len(temp_fields)
        vals = [self._sanitize_temp(s) for s in temp_fields[:MAX_SENSORS]]
        return t_sec, vals, valve, mode_char

    # ── Cleanup ───────────────────────────────────────────────────────────
    def cleanup(self) -> None:
        """Stop timers, close files/serial, close figure. Never touch UI here."""
        try:
            if self.anim and self.anim.event_source:
                self.anim.event_source.stop()
        except Exception:
            pass
        # Close log file without touching UI (prevents TclError on shutdown)
        self.stop_logging(ui_update=False)
        try:
            if self.serial and self.serial.is_open:
                self.serial.close()
        except SerialException:
            pass
        try:
            plt.close(self.figure)
        except Exception:
            pass


def main() -> None:
    app = TCMonitorApp()
    app.run()


if __name__ == "__main__":
    main()
 