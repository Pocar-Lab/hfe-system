#!/usr/bin/env bash
set -euo pipefail

# Start the FastAPI supervisor using uvicorn, binding to all interfaces so the
# WebSocket and REST endpoints are reachable from remote clients.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

HOST="${HOST_OVERRIDE:-0.0.0.0}"
PORT="${PORT_OVERRIDE:-8000}"
LOG_DIR="${ROOT_DIR}/logs"
LOG_FILE="${LOG_DIR}/supervisor.log"

SUPERVISOR_PATTERN="uvicorn .*supervisor\\.app:app"

readarray -t existing_pids < <(
  {
    if command -v pgrep >/dev/null 2>&1; then
      pgrep -f "$SUPERVISOR_PATTERN"
    else
      ps -eo pid=,args= | awk '/uvicorn/ && /supervisor.app:app/ {print $1}'
    fi
  } || true
)

if ((${#existing_pids[@]} > 0)); then
  echo "Found existing supervisor process(es): ${existing_pids[*]}"
  echo "Stopping existing supervisor…"
  for pid in "${existing_pids[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done

  deadline=$((SECONDS + 5))
  for pid in "${existing_pids[@]}"; do
    while kill -0 "$pid" 2>/dev/null; do
      if ((SECONDS >= deadline)); then
        echo "Process $pid did not terminate in time; sending SIGKILL."
        kill -9 "$pid" 2>/dev/null || true
        break
      fi
      sleep 0.2
    done
  done

  echo "Previous supervisor instance stopped."
fi

FLASH_FIRMWARE="${FLASH_FIRMWARE:-1}"
if [[ "${FLASH_FIRMWARE,,}" != "0" && "${FLASH_FIRMWARE,,}" != "false" && "${FLASH_FIRMWARE,,}" != "no" ]]; then
  PIO_ACTIVATE="${PIO_ACTIVATE:-}"
  pio_env_sourced="0"
  declare -a _pio_candidates=()
  if [[ -n "${PIO_ACTIVATE}" ]]; then
    _pio_candidates+=("${PIO_ACTIVATE}")
  fi
  _pio_candidates+=(
    "${ROOT_DIR}/firmware/.venv/bin/activate"
    "${ROOT_DIR}/firmware/.platformio/penv/bin/activate"
    "${ROOT_DIR}/firmware/.pio/penv/bin/activate"
    "/home/pocar-lab/platformio-venv/bin/activate"
    "${HOME}/.platformio/penv/bin/activate"
  )
  for candidate in "${_pio_candidates[@]}"; do
    if [[ -f "${candidate}" ]]; then
      # shellcheck disable=SC1090  # allow dynamic source; paths are user configurable
      source "${candidate}"
      pio_env_sourced="1"
      break
    fi
  done
  if [[ "${pio_env_sourced}" != "1" ]]; then
    echo "No PlatformIO virtualenv found in firmware folder or defaults; set PIO_ACTIVATE to point at a valid environment." >&2
  fi
  if ! command -v platformio >/dev/null 2>&1; then
    echo "platformio not found on PATH. Install PlatformIO CLI or set FLASH_FIRMWARE=0 to skip firmware upload." >&2
    exit 1
  fi
  if ! platformio --version >/dev/null 2>&1; then
    echo "platformio command failed to run (possibly incompatible version). Activate a newer PlatformIO environment or set FLASH_FIRMWARE=0." >&2
    exit 1
  fi
  echo "Rebuilding and uploading firmware (set FLASH_FIRMWARE=0 to skip)…"
  platformio run -d firmware -t upload
  if [[ "${pio_env_sourced}" == "1" && "$(type -t deactivate 2>/dev/null)" == "function" ]]; then
    deactivate || true
  fi
else
  echo "Skipping firmware rebuild/upload (FLASH_FIRMWARE=${FLASH_FIRMWARE})."
fi

PY_ACTIVATE="${PY_ACTIVATE:-${ROOT_DIR}/.venv/bin/activate}"
if [[ -f "${PY_ACTIVATE}" ]]; then
  # shellcheck disable=SC1090  # user-configurable path
  source "${PY_ACTIVATE}"
else
  echo "Python virtualenv not found at ${PY_ACTIVATE}; supervisor dependencies may be missing." >&2
fi

if ! command -v uvicorn >/dev/null 2>&1; then
  echo "uvicorn not found on PATH. Install dependencies first: pip install -e .[analysis,notebooks]" >&2
  exit 1
fi

if ! python -c "import yaml" >/dev/null 2>&1; then
  echo "PyYAML is not installed in the active environment. Run: source ${PY_ACTIVATE} && pip install -e .[analysis,notebooks]" >&2
  exit 1
fi

mkdir -p "${LOG_DIR}"
: >"${LOG_FILE}"

echo "Starting supervisor on ${HOST}:${PORT} (logs: ${LOG_FILE})"

nohup uvicorn supervisor.app:app --host "${HOST}" --port "${PORT}" \
  >"${LOG_FILE}" 2>&1 &
SUP_PID=$!

if command -v disown >/dev/null 2>&1; then
  disown "${SUP_PID}" >/dev/null 2>&1 || true
fi

echo "Supervisor started in background with PID ${SUP_PID}."
