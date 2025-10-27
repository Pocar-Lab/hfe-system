#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

export PYTHONUNBUFFERED=1
export SUPERVISOR_TOKEN=${SUPERVISOR_TOKEN:-}
export REPO_ROOT

read_host_port() {
python - <<'PY'
import os
import sys
from pathlib import Path

import yaml

repo_root = Path(os.environ["REPO_ROOT"])
cfg_path = repo_root / "config" / "config.yaml"
try:
    cfg = yaml.safe_load(cfg_path.read_text()) or {}
    server = cfg.get("server") or {}
    host = server.get("host", "127.0.0.1")
    port = int(server.get("port", 8000))
except FileNotFoundError:
    print(f"Missing config: {cfg_path}", file=sys.stderr)
    sys.exit(1)
except Exception as exc:
    print(f"Failed to parse {cfg_path}: {exc}", file=sys.stderr)
    sys.exit(1)
print(f"{host} {port}")
PY
}

read_host_port | {
    read HOST PORT || exit 1
    cd "${SCRIPT_DIR}"
    uvicorn app:app --host "${HOST}" --port "${PORT}"
}
