#!/usr/bin/env bash
set -euo pipefail

# Start the FastAPI supervisor using uvicorn, binding to all interfaces so the
# WebSocket and REST endpoints are reachable from remote clients.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

HOST="${HOST_OVERRIDE:-0.0.0.0}"
PORT="${PORT_OVERRIDE:-8000}"

if ! command -v uvicorn >/dev/null 2>&1; then
  echo "uvicorn not found on PATH. Install dependencies first: pip install -r requirements.txt" >&2
  exit 1
fi

echo "Starting supervisor on ${HOST}:${PORT} (Ctrl+C to stop)…"
exec uvicorn supervisor.app:app --host "${HOST}" --port "${PORT}"
