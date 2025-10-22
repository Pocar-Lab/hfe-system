#!/usr/bin/env bash
source .venv/bin/activate
set -euo pipefail

# Helper to launch the Tk plot client with the correct SUP_HOST value.
# Usage: run_plot_live.sh [host-or-host:port] [port]
#  - If host:port is supplied as the first arg, port argument is optional.
#  - If only host is supplied, port defaults to 8000.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RAW_HOST="${1:-${SUP_HOST:-127.0.0.1}}"
PORT="${2:-8000}"

if [[ "${RAW_HOST}" == *:* ]]; then
  export SUP_HOST="${RAW_HOST}"
else
  export SUP_HOST="${RAW_HOST}:${PORT}"
fi

echo "Connecting to supervisor at ${SUP_HOST}"
exec python3 clients/plot_live.py
