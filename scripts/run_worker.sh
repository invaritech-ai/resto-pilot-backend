#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

# Helps prevent macOS fork-safety issues with some native deps.
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY="${OBJC_DISABLE_INITIALIZE_FORK_SAFETY:-YES}"

LOGLEVEL="${CELERY_LOGLEVEL:-info}"
CONCURRENCY="${CELERY_CONCURRENCY:-4}"

exec python -m celery -A app.workers.celery_app.celery_app worker -l "$LOGLEVEL" --concurrency "$CONCURRENCY"

