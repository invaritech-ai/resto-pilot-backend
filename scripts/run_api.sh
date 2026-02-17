#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

exec python -m uvicorn app.main:app --reload --host "$HOST" --port "$PORT"

