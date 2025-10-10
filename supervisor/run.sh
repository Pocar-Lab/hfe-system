#!/usr/bin/env bash
set -euo pipefail
export PYTHONUNBUFFERED=1
export SUPERVISOR_TOKEN=${SUPERVISOR_TOKEN:-}
uvicorn app:app --host $(python -c 'import json;print(json.load(open("config/config.yaml"))["server"]["host"])') --port $(python -c 'import json;print(json.load(open("config/config.yaml"))["server"]["port"])')
