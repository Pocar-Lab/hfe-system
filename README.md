# HFE System

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
- `cd firmware`
- `platformio run -t upload`

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
- Configure `config/config.yaml` with the serial port, bind host/port, and (optionally) `auth_token`.
- Launch the API: `bash scripts/start_supervisor.sh`
- Override at runtime with env vars:
  `SUPERVISOR_TOKEN` (auth), `SUP_HOST` (host:port for clients), `SUP_API` (HTTP base URL).
- Typical SSH workflow:
  1. SSH into the lab host (no X forwarding needed).  
  2. Activate the project environment if applicable (`source .venv/bin/activate`).  
  3. Start the supervisor in one terminal:  
     `bash scripts/start_supervisor.sh`  
     Leave it running; it binds to `0.0.0.0:8000` for remote clients.

## Live Clients
- Web GUI: `http://<host>:8000/ui`
  - Dark/light aware layout with responsive plot, live valve state, and per-sensor readouts.
  - Adjust setpoint/hysteresis/telemetry interval and choose which sensors feed the Auto/average logic via checkboxes; view each sensor's latest value as a chip.
  - Forward over SSH: `ssh -L 8000:localhost:8000 <user>@<host>` then browse `http://localhost:8000/ui`
  - Copy link with `?token=...` if auth enabled, or load without token when `Auth required: False`.
  - The logging toggle starts streaming telemetry to `data/raw/<timestamp>.csv` on the supervisor host while buffering a local download; pressing it again ("Stop Logging") stops the server-side log and saves a copy to your browser.
- Serial/UI logger (legacy): `python3 scripts/plot_temp.py` (set `PORT` if needed).

## Data Analysis
- Command-line pipeline: `hfe-hx --input data/raw/<file>.csv`
  - Outputs go to `data/processed/` by default (configurable via flags).
- Notebook: open `analysis/notebooks/HX_performance_analysis.ipynb` after installing the analysis package above.

## Git / GitHub
- Use the VS Code Source Control pane to commit and push changes.
