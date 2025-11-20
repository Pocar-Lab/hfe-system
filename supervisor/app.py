# supervisor/app.py — FastAPI supervisor with token auth + WS streaming
from __future__ import annotations

import os
import csv
import json
import math
import asyncio
import threading
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import yaml
from glob import glob
from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    Header,
    Query,
    Body,
)
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# ─────────────────────────── logging ───────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("supervisor")

# ─────────────────────── config + token load ───────────────────
HERE = Path(__file__).resolve()
REPO = HERE.parents[1]  # repo root (…/HFE_System)
CFG_PATH = REPO / "config" / "config.yaml"
if not CFG_PATH.exists():
    raise FileNotFoundError(f"Missing config file: {CFG_PATH}")

with CFG_PATH.open("r") as f:
    CFG = yaml.safe_load(f) or {}

ENV_TOKEN = (os.getenv("SUPERVISOR_TOKEN") or "").strip()
CFG_TOKEN = (CFG.get("server", {}).get("auth_token") or "").strip()
AUTH_TOKEN = ENV_TOKEN or CFG_TOKEN

log.info(
    "Auth required: %s; token prefix: %s",
    bool(AUTH_TOKEN),
    (AUTH_TOKEN[:8] + "…") if AUTH_TOKEN else "(none)",
)

# Ensure data directory exists if configured (even if we don't write yet)
data_dir = (Path(CFG.get("logging", {}).get("parquet_dir", "")) if CFG.get("logging") else None)
if data_dir:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

# ─────────────────────── auth helper ──────────────────────────
def require_auth(authorization: Optional[str]) -> None:
    """
    Require a matching token if AUTH_TOKEN is set.
    Accepts either 'Bearer <token>' or raw '<token>'.
    """
    if not AUTH_TOKEN:
        return  # dev mode: open
    if not authorization:
        raise HTTPException(401, "Unauthorized")
    supplied = authorization.split()[-1]
    if supplied != AUTH_TOKEN:
        raise HTTPException(401, "Unauthorized")


# ────────────────────── serial parsing helpers ─────────────────
def parse_serial_payload(raw: bytes) -> Optional[dict]:
    """
    Convert a serial line (CSV or JSON) into a telemetry dict compatible with clients.
    """
    text = raw.decode("utf-8", errors="ignore").strip()
    if not text or text.startswith("#"):
        return None
    # JSON payload
    try:
        msg = json.loads(text)
    except json.JSONDecodeError:
        msg = None
    if isinstance(msg, dict):
        if "type" not in msg:
            msg["type"] = "telemetry"
        temps_msg = msg.get("temps")
        if isinstance(temps_msg, list) and "tC" not in msg:
            for item in temps_msg:
                try:
                    val = float(item)
                except Exception:
                    continue
                if math.isfinite(val):
                    msg["tC"] = val
                    break
        return msg
    if text.startswith("time_s"):
        # Header line; ignore after logging
        return {"type": "header", "line": text}
    parts = [p.strip() for p in text.split(",")]
    if len(parts) < 3:
        return {"type": "raw", "line": text}
    try:
        t_sec = float(parts[0])
    except ValueError:
        return {"type": "raw", "line": text}
    temps: list[float] = []
    for field in parts[1:-2]:
        try:
            value = float(field)
            if DETECT_ZERO_AS_NC and abs(value) < 1e-12:
                value = math.nan
        except ValueError:
            value = math.nan
        temps.append(value)
    try:
        valve = int(parts[-2])
    except ValueError:
        valve = None
    mode = parts[-1][:1] if parts[-1] else ""
    safe_temps: list[float | None] = []
    for v in temps:
        if isinstance(v, float) and math.isfinite(v):
            safe_temps.append(v)
        else:
            safe_temps.append(None)

    payload = {
        "type": "telemetry",
        "t": t_sec,
        "temps": safe_temps,
        "valve": valve,
        "mode": mode,
    }
    for item in safe_temps:
        if isinstance(item, float) and math.isfinite(item):
            payload["tC"] = item
            break
    return payload

DETECT_ZERO_AS_NC = True

MAX_LOG_SENSORS = 10
RAW_LOG_DIR = REPO / "data" / "raw"

def candidate_serial_ports() -> list[str]:
    ports: list[str] = []
    cfg_port = str(CFG.get("serial", {}).get("port") or "").strip()
    if cfg_port:
        ports.append(cfg_port)
    for pattern in ("/dev/ttyACM*", "/dev/ttyUSB*"):
        for p in sorted(glob(pattern)):
            ports.append(p)
    # de-dup while preserving order
    seen = set()
    unique = []
    for p in ports:
        if p not in seen:
            unique.append(p)
            seen.add(p)
    return unique


def _init_logging_state(state) -> None:
    state.log_enabled = False
    state.log_path = None
    state.log_file = None
    state.log_writer = None
    state.log_rows = 0


def _sanitize_filename(name: str) -> str:
    candidate = Path(name).name.strip()
    candidate = candidate.replace(" ", "_")
    allowed = "".join(c for c in candidate if c.isalnum() or c in {"-", "_", "."})
    if not allowed:
        raise ValueError("Invalid filename")
    if not allowed.lower().endswith(".csv"):
        allowed += ".csv"
    if allowed.startswith("."):
        raise ValueError("Invalid filename")
    return allowed


def _logging_status(state) -> dict:
    path = getattr(state, "log_path", None)
    return {
        "ok": True,
        "active": bool(getattr(state, "log_enabled", False)),
        "path": str(path) if isinstance(path, Path) else None,
        "filename": path.name if isinstance(path, Path) else None,
        "rows": int(getattr(state, "log_rows", 0) or 0),
    }


def _start_logging(state, filename: Optional[str] = None) -> dict:
    if getattr(state, "log_enabled", False):
        raise RuntimeError("Logging already active")
    RAW_LOG_DIR.mkdir(parents=True, exist_ok=True)
    if filename:
        safe_name = _sanitize_filename(filename)
    else:
        safe_name = f"tc_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    path = RAW_LOG_DIR / safe_name
    fh = path.open("w", newline="", encoding="utf-8")
    writer = csv.writer(fh)
    header = ["time_s"] + [f"temp{i}_C" for i in range(MAX_LOG_SENSORS)] + ["valve", "mode"]
    writer.writerow(header)
    fh.flush()
    state.log_enabled = True
    state.log_path = path
    state.log_file = fh
    state.log_writer = writer
    state.log_rows = 0
    return _logging_status(state)


def _stop_logging(state, *, cleanup: bool = False) -> Optional[dict]:
    if not getattr(state, "log_enabled", False):
        return None if cleanup else {"ok": False, "detail": "logging inactive"}
    fh = getattr(state, "log_file", None)
    path = getattr(state, "log_path", None)
    rows = getattr(state, "log_rows", 0)
    if fh:
        try:
            fh.flush()
            fh.close()
        except Exception:
            pass
    state.log_enabled = False
    state.log_file = None
    state.log_writer = None
    state.log_path = None
    state.log_rows = 0
    result = {
        "ok": True,
        "path": str(path) if path else None,
        "filename": path.name if isinstance(path, Path) else None,
        "rows": rows,
        "active": False,
    }
    return None if cleanup else result


def _maybe_log_telemetry(state, payload: dict) -> None:
    if not getattr(state, "log_enabled", False):
        return
    writer = getattr(state, "log_writer", None)
    fh = getattr(state, "log_file", None)
    if writer is None or fh is None:
        return
    if not isinstance(payload, dict) or payload.get("type") != "telemetry":
        return

    temps = payload.get("temps") or []
    if not isinstance(temps, list):
        temps = []

    row: list[str] = []
    t_val = payload.get("t")
    if isinstance(t_val, (int, float)) and math.isfinite(float(t_val)):
        row.append(f"{float(t_val):.3f}")
    else:
        row.append("")

    for idx in range(MAX_LOG_SENSORS):
        value = temps[idx] if idx < len(temps) else None
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            row.append(f"{float(value):.2f}")
        else:
            row.append("nan")

    valve = payload.get("valve")
    row.append(str(int(valve)) if isinstance(valve, (int, float)) else "0")
    mode = payload.get("mode") or ""
    row.append(str(mode)[:1])

    try:
        writer.writerow(row)
        fh.flush()
        state.log_rows = getattr(state, "log_rows", 0) + 1
    except Exception:
        pass

# ───────────────────── optional serial support ─────────────────
try:
    import serial_asyncio  # provided by pyserial-asyncio
except Exception:
    serial_asyncio = None
    log.warning("pyserial-asyncio not available; running without serial asyncio support")

try:
    import serial  # pyserial
except Exception:
    serial = None

# ───────────────────── WS clients registry ─────────────────────
clients: set[WebSocket] = set()


# ───────────────────────── lifespan ────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup:
      - create queues
      - start broadcaster
      - try to attach to serial and push JSON lines into q_live
      - (if no serial) start a dummy telemetry feeder
    Shutdown:
      - cancel tasks, close serial transport
    """
    app.state.q_live: asyncio.Queue = asyncio.Queue(maxsize=10000)
    app.state.tasks: list[asyncio.Task] = []
    app.state.ser_transport = None
    app.state.ser_thread = None
    app.state.ser_thread_stop = threading.Event()
    app.state.ser_handle = None
    app.state.ser_lock = threading.Lock()
    _init_logging_state(app.state)

    async def broadcaster():
        """Fan-out any message placed on q_live to all connected WS clients."""
        while True:
            msg = await app.state.q_live.get()
            _maybe_log_telemetry(app.state, msg)
            dead: list[WebSocket] = []
            # Broadcast to clients
            for ws in list(clients):
                try:
                    await ws.send_text(json.dumps(msg))
                except Exception:
                    dead.append(ws)
            # Clean up broken sockets
            for ws in dead:
                try:
                    clients.remove(ws)
                except KeyError:
                    pass
            app.state.q_live.task_done()

    # Optional: dummy telemetry when serial is unavailable
    async def dummy_feeder():
        import time, random
        while True:
            await asyncio.sleep(1.0)
            pump_cmd_pct = 0.0  # keep stable to avoid UI confusion when dummy is used
            pump_freq_pct = 0.0
            pump_freq_hz = 0.0
            temps = [24.5 + 1.5 * (2.0 * random.random() - 1.0) for _ in range(4)]
            temps += [None] * (MAX_LOG_SENSORS - len(temps))
            app.state.q_live.put_nowait(
                {
                    "type": "telemetry",
                    "t": time.time(),
                    "tC": temps[0],
                    "temps": temps,
                    "valve": int(random.random() > 0.5),
                    "fault": False,
                    "pump": {
                        "cmd_pct": pump_cmd_pct,
                        "cmd_hz": pump_cmd_pct / 100.0 * 60.0,
                        "freq_hz": pump_freq_hz,
                        "freq_pct": pump_freq_pct,
                        "output_current_a": 0.5 + 0.1 * (random.random() - 0.5),
                        "output_current_pct": 18.0 + 3.0 * (random.random() - 0.5),
                        "output_voltage_v": 12.0 + 0.5 * (random.random() - 0.5),
                        "output_voltage_pct": 5.0 + 0.2 * (random.random() - 0.5),
                        "input_power_w": 30.0 + 5.0 * (random.random() - 0.5),
                        "input_power_pct": 8.0 + 2.0 * (random.random() - 0.5),
                        "max_freq_hz": 60.0,
                    },
                }
            )

    # Try to open serial (if library present)
    baud = int(CFG.get("serial", {}).get("baudrate", 115200))
    connected = False
    if serial_asyncio:
        loop = asyncio.get_running_loop()

        class Proto(asyncio.Protocol):
            def __init__(self, q: asyncio.Queue):
                self.q = q
                self.buf = b""

            def data_received(self, data: bytes):
                self.buf += data
                while b"\n" in self.buf:
                    line, self.buf = self.buf.split(b"\n", 1)
                    payload = parse_serial_payload(line)
                    if payload is None:
                        continue
                    try:
                        self.q.put_nowait(payload)
                    except asyncio.QueueFull:
                        try:
                            _ = self.q.get_nowait()
                            self.q.task_done()
                            self.q.put_nowait(payload)
                        except Exception:
                            pass

        for port in candidate_serial_ports():
            try:
                transport, _ = await serial_asyncio.create_serial_connection(
                    loop, lambda: Proto(app.state.q_live), port, baudrate=baud
                )
                app.state.ser_transport = transport
                app.state.ser_handle = transport
                connected = True
                log.info("Serial connected: %s @ %s", port, baud)
                break
            except Exception as e:
                log.error("Serial unavailable on %s: %s", port, e)

    if not connected and serial:
        # Fallback: blocking reader thread using pyserial
        def serial_reader(stop_evt: threading.Event, q: asyncio.Queue, port: str):
            try:
                with serial.Serial(port, baud, timeout=0.2) as ser:
                    app.state.ser_handle = ser
                    log.info("Serial (thread) connected: %s @ %s", port, baud)
                    buf = b""
                    while not stop_evt.is_set():
                        try:
                            chunk = ser.read_until(b"\n")
                        except Exception:
                            continue
                        if not chunk:
                            continue
                        payload = parse_serial_payload(chunk)
                        if payload is None:
                            continue
                        try:
                            q.put_nowait(payload)
                        except asyncio.QueueFull:
                            try:
                                _ = q.get_nowait()
                                q.task_done()
                                q.put_nowait(payload)
                            except Exception:
                                pass
                    app.state.ser_handle = None
            except Exception as exc:
                log.error("Serial thread failed on %s: %s", port, exc)

        for port in candidate_serial_ports():
            try:
                app.state.ser_thread_stop.clear()
                app.state.ser_thread = threading.Thread(
                    target=serial_reader,
                    args=(app.state.ser_thread_stop, app.state.q_live, port),
                    daemon=True,
                )
                app.state.ser_thread.start()
                connected = True
                break
            except Exception as e:
                log.error("Serial unavailable (thread) on %s: %s", port, e)

    if not connected:
        log.error("Serial unavailable; no telemetry will be broadcast. Candidates tried: %s", candidate_serial_ports())

    # Start broadcaster and (if needed) dummy feeder
    app.state.tasks.append(asyncio.create_task(broadcaster()))
    allow_dummy = os.getenv("SUP_ALLOW_DUMMY", "0").lower() in {"1", "true", "yes"}
    if app.state.ser_transport is None and app.state.ser_thread is None:
        if allow_dummy:
          app.state.tasks.append(asyncio.create_task(dummy_feeder()))
        else:
          log.error("Serial unavailable and SUP_ALLOW_DUMMY is not enabled; no telemetry will be broadcast.")

    # Hand control to FastAPI
    try:
        yield
    finally:
        # Shutdown
        _stop_logging(app.state, cleanup=True)
        for t in app.state.tasks:
            t.cancel()
        await asyncio.gather(*app.state.tasks, return_exceptions=True)
        if app.state.ser_transport:
            try:
                app.state.ser_transport.close()
            except Exception:
                pass
        if app.state.ser_thread:
            try:
                app.state.ser_thread_stop.set()
                app.state.ser_thread.join(timeout=1.0)
            except Exception:
                pass
        app.state.ser_handle = None


# ───────────────────────── FastAPI app ─────────────────────────
app = FastAPI(lifespan=lifespan)

WEB_UI_DIR = REPO / "clients" / "web"
if WEB_UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=WEB_UI_DIR, html=True), name="ui")

# Public health (no auth)
@app.get("/health")
async def health():
    return {"ok": True}


# Protected ping (auth required)
@app.get("/api/ping")
async def api_ping(authorization: Optional[str] = Header(default=None)):
    require_auth(authorization)
    return {"ok": True}


# Command endpoint (auth required). Sends a JSON line to serial if available.
@app.post("/api/command")
async def api_command(body: dict, authorization: Optional[str] = Header(default=None)):
    require_auth(authorization)
    cmd_text = str(body.get("cmd") or body.get("command") or "").strip()
    if cmd_text:
        try:
            line = (cmd_text + "\n").encode("ascii")
        except UnicodeEncodeError:
            raise HTTPException(400, "Command must be ASCII-compatible")
    else:
        line = (json.dumps(body) + "\n").encode("utf-8")
    if getattr(app.state, "ser_transport", None):
        try:
            app.state.ser_transport.write(line)
            return JSONResponse({"ok": True})
        except Exception as e:
            raise HTTPException(500, f"Serial write failed: {e}")

    # Thread/pyserial path
    ser_handle = getattr(app.state, "ser_handle", None)
    ser_lock = getattr(app.state, "ser_lock", None)
    if ser_handle and ser_lock:
        with ser_lock:
            try:
                ser_handle.write(line)
                ser_handle.flush()
                return JSONResponse({"ok": True})
            except Exception as e:
                raise HTTPException(500, f"Serial write failed: {e}")

    # No serial available; return 503 but echo the command for debugging
    return JSONResponse({"ok": False, "echo": body, "detail": "serial unavailable"}, status_code=503)


@app.get("/api/logging/status")
async def api_logging_status(authorization: Optional[str] = Header(default=None)):
    require_auth(authorization)
    return _logging_status(app.state)


@app.post("/api/logging/start")
async def api_logging_start(
    body: dict = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
):
    require_auth(authorization)
    filename = ""
    if isinstance(body, dict):
        filename = str(body.get("filename") or "").strip()
    try:
        status = _start_logging(app.state, filename or None)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return status


@app.post("/api/logging/stop")
async def api_logging_stop(authorization: Optional[str] = Header(default=None)):
    require_auth(authorization)
    result = _stop_logging(app.state)
    return result or {"ok": False, "detail": "logging inactive"}


# WebSocket endpoint. Use /ws?token=XYZ  (token optional if AUTH_TOKEN empty)
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: Optional[str] = Query(default=None)):
    if AUTH_TOKEN:
        if not token or token != AUTH_TOKEN:
            await ws.close(code=1008)  # Policy Violation
            return
    await ws.accept()
    clients.add(ws)
    try:
        # Keepalive/read loop (clients may send pings or no-op messages)
        while True:
            try:
                await ws.receive_text()
            except asyncio.CancelledError:
                raise
            except WebSocketDisconnect:
                break
            except RuntimeError as exc:
                # Starlette raises RuntimeError after disconnect; treat as closed
                log.debug("WS receive after disconnect: %s", exc)
                break
            except Exception as exc:
                # Many clients never send anything; small sleep avoids a tight loop
                log.debug("WS receive error: %s", exc)
                await asyncio.sleep(5.0)
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)
