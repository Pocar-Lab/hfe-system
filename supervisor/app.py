# supervisor/app.py — FastAPI supervisor with token auth + WS streaming
from __future__ import annotations

import os
import json
import math
import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional

import yaml
from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    Header,
    Query,
)
from fastapi.responses import JSONResponse

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
    payload = {
        "type": "telemetry",
        "t": t_sec,
        "temps": temps,
        "valve": valve,
        "mode": mode,
    }
    if temps:
        first = temps[0]
        if isinstance(first, float) and math.isfinite(first):
            payload["tC"] = first
    return payload

DETECT_ZERO_AS_NC = True

# ───────────────────── optional serial support ─────────────────
try:
    import serial_asyncio  # provided by pyserial-asyncio
except Exception:
    serial_asyncio = None
    log.warning("pyserial-asyncio not available; running without serial")

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

    async def broadcaster():
        """Fan-out any message placed on q_live to all connected WS clients."""
        while True:
            msg = await app.state.q_live.get()
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
            app.state.q_live.put_nowait(
                {
                    "type": "telemetry",
                    "t": time.time(),
                    "tC": 24.5 + 1.5 * (2.0 * random.random() - 1.0),
                    "valve": int(random.random() > 0.5),
                    "fault": False,
                }
            )

    # Try to open serial (if library present)
    if serial_asyncio:
        try:
            loop = asyncio.get_running_loop()
            port = CFG["serial"]["port"]
            baud = int(CFG["serial"]["baudrate"])

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

            transport, _ = await serial_asyncio.create_serial_connection(
                loop, lambda: Proto(app.state.q_live), port, baudrate=baud
            )
            app.state.ser_transport = transport
            log.info("Serial connected: %s @ %s", port, baud)

        except Exception as e:
            log.error("Serial unavailable: %s. Starting API without hardware.", e)

    # Start broadcaster and (if needed) dummy feeder
    app.state.tasks.append(asyncio.create_task(broadcaster()))
    if app.state.ser_transport is None:
        app.state.tasks.append(asyncio.create_task(dummy_feeder()))

    # Hand control to FastAPI
    try:
        yield
    finally:
        # Shutdown
        for t in app.state.tasks:
            t.cancel()
        await asyncio.gather(*app.state.tasks, return_exceptions=True)
        if app.state.ser_transport:
            try:
                app.state.ser_transport.close()
            except Exception:
                pass


# ───────────────────────── FastAPI app ─────────────────────────
app = FastAPI(lifespan=lifespan)

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
    else:
        # No serial available; return 503 but echo the command for debugging
        return JSONResponse({"ok": False, "echo": body, "detail": "serial unavailable"}, status_code=503)


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
