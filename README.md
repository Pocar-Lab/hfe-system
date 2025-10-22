# HFE System

## Environment Setup
- (Optional) Activate the PlatformIO environment for firmware work:
  `source /home/pocar-lab/platformio-venv/bin/activate`
- Create a Python virtual environment for the supervisor + tooling:
  `python3 -m venv .venv && source .venv/bin/activate`
- Install Python dependencies:
  `pip install -r supervisor/requirements.txt`
- Install the analysis helpers for notebooks/CLI:
  `pip install -e analysis`

## Firmware Workflow
- `cd firmware`
- `platformio run -t upload`

## Supervisor API
- Configure `config/config.yaml` with the serial port, bind host/port, and (optionally) `auth_token`.
- Launch the API: `cd supervisor && ./run.sh`
- Override at runtime with env vars:
  `SUPERVISOR_TOKEN` (auth), `SUP_HOST` (host:port for clients), `SUP_API` (HTTP base URL).

## Live Clients
- Serial/UI logger: `python3 scripts/plot_temp.py` (set `PORT` if needed).
- Websocket Tk UI: `python3 clients/tk_client.py`
  - Reads `SUP_HOST` and `SUPERVISOR_TOKEN` to find the supervisor.

## Data Analysis
- Command-line pipeline: `hfe-hx --input data/raw/<file>.csv`
  - Outputs go to `data/processed/` and `data/reports/` (configurable via flags).
- Notebook: open `analysis/notebooks/HX_performance_analysis.ipynb` after installing the analysis package above.

## Git / GitHub
- Use the VS Code Source Control pane to commit and push changes.
