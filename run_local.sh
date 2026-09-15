#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

./.venv/bin/python -m pip install -q -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  echo "已创建 .env（默认 DRY_RUN=true）"
fi

# One shared .env is used by both strategies. If the new project has missing or
# malformed local credentials and the previous executor exists on this Mac,
# repair them locally without printing any secret value.
./.venv/bin/python -m app.credentials

exec ./.venv/bin/python terminal.py
