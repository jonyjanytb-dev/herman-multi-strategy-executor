#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PYTHON_BIN="${PYTHON_BIN:-python3}"
if [ ! -d .venv ]; then
  "$PYTHON_BIN" -m venv .venv
fi
./.venv/bin/python -m pip install -q -r requirements.txt
if [ ! -f .env ]; then
  cp .env.example .env
  echo "已创建 .env；默认 DRY_RUN=true。"
fi
exec ./.venv/bin/python terminal.py
