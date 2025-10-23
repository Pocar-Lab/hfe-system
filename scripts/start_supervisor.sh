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

if ! command -v uvicorn >/dev/null 2>&1; then
  echo "uvicorn not found on PATH. Install dependencies first: pip install -r requirements.txt" >&2
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
