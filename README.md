# HFE System

FastAPI supervisor, Arduino Mega firmware, browser UI, and ORCA analysis tools for the HFE recirculation/cooling system.

## Repository Layout
- `firmware/`: PlatformIO project for the Arduino Mega controller.
- `supervisor/`: FastAPI serial bridge, command API, WebSocket telemetry, and server-side logging.
- `clients/web/`: static browser UI served by the supervisor at `/ui`.
- `analysis/`: installable ORCA analysis package, notebooks, and reference documents.
- `config/config.yaml`: local serial, server, logging, and flow-meter unit configuration.
- `data/raw/`: runtime logger output; ignored by git.
- `data/processed/`: curated/generated analysis outputs that are tracked when useful.

## Environment Setup
- (Optional) Activate the PlatformIO environment for firmware work:
  `source /home/pocar-lab/platformio-venv/bin/activate`
- Create a Python virtual environment for the supervisor + tooling:
  `python3 -m venv .venv && source .venv/bin/activate`
- Install Python dependencies:
  `pip install -e .[analysis,notebooks]`
- Install the analysis helpers for notebooks/CLI:
  `pip install -e analysis`

## Firmware Workflow
- Upload the main controller firmware:
  `platformio run -d firmware -e megaatmega2560 -t upload`
- Upload the pump-test sketch, if needed:
  `platformio run -d firmware -e pump_test -t upload`
- `firmware/platformio.ini` pins the main upload/monitor port to the Arduino-by-id path; update it if the board changes.

## Arduino Connection / Reconnection
First-time connection (or new Arduino):
1. Plug the Arduino in via USB.
2. Find the serial port (Linux: `ls /dev/ttyACM* /dev/ttyUSB*`) and set `serial.port` in `config/config.yaml`.
3. Start the supervisor (this uploads firmware by default): `bash scripts/start_supervisor.sh`
4. Confirm the serial link in `logs/supervisor.log` (look for `Serial connected:`). If you see `Serial unavailable`, fix the port and restart the supervisor.

After a computer reboot/reset or after unplugging/replugging the Arduino:
1. Verify the Arduino is connected and the port name in `config/config.yaml` is still correct.
2. Restart the supervisor to reconnect to serial: `bash scripts/start_supervisor.sh`
3. If you do not need to reflash firmware, skip it with `FLASH_FIRMWARE=0 bash scripts/start_supervisor.sh`.

## Supervisor API
- Configure `config/config.yaml` with the serial port/baudrate, flow-meter source units, and optionally `server.auth_token`.
- For the Global Industrial 318506 scale, set `scale.port`, `scale.baudrate`, `scale.byte_format`, and `scale.layout` to match the scale's `USER-COM2-*` menu settings. The default USB virtual RS232 setup here is `COM2`, `9600`, `8N1`, `MULTPL`.
- Launch the API with the project helper:
  `bash scripts/start_supervisor.sh`
  - This helper uploads firmware by default, then starts `uvicorn supervisor.app:app` in the background.
  - Default bind is `0.0.0.0:8000`; override with `HOST_OVERRIDE` and `PORT_OVERRIDE`.
  - Useful runtime env vars: `SUPERVISOR_TOKEN` (auth token), `FLASH_FIRMWARE=0` (skip upload), `PIO_ACTIVATE` (PlatformIO venv), `PY_ACTIVATE` (Python venv), `SUP_ALLOW_DUMMY=1` (dummy telemetry when serial is unavailable).
- To run in the foreground using the `server.host` / `server.port` values from `config/config.yaml`, use:
  `bash supervisor/run.sh`
- Typical SSH workflow:
  1. SSH into the lab host (no X forwarding needed).  
  2. Activate the project environment if applicable (`source .venv/bin/activate`).  
  3. Start the supervisor in one terminal:  
     `bash scripts/start_supervisor.sh`  
     Leave it running; by default it binds to `0.0.0.0:8000` for remote clients.

## Live Clients
- Web GUI: `http://<host>:8000/ui`
  - Dark/light aware layout with responsive temperature/pressure plots, live valve state, and per-sensor readouts.
  - Control pump command, LN valve state, heaters, and auto-mode targets: HFE goal, HX limit, HX approach, and hysteresis.
  - View VFD, pressure, flow-meter, fluid-property, safety-interlock, and heater telemetry when the firmware reports it.
  - Forward over SSH: `ssh -L 8000:localhost:8000 <user>@<host>` then browse `http://localhost:8000/ui`
  - Copy link with `?token=...` if auth enabled, or load without token when `Auth required: False`.
  - The logging toggle streams telemetry to `data/raw/log_<YYYYMMDD>_<HHMMSS>.csv` on the supervisor host while buffering a local browser download; pressing it again ("Stop Logging") stops the server-side log and saves a copy to your browser when rows were buffered locally.
- Serial/UI logger (legacy): `python3 scripts/plot_temp.py` (edit the `PORT` constant in that script if needed).

## Data Analysis
- Command-line pipeline: `hfe-hx --input data/raw/<file>.csv`
  - Outputs go to `data/processed/` by default (configurable via flags).
- Notebook: open `analysis/notebooks/HX_performance_analysis.ipynb` after installing the analysis package above.

## Git / GitHub
- Use the VS Code Source Control pane to commit and push changes.
