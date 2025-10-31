#!/usr/bin/env bash
set -euo pipefail

echo "[entrypoint] running database migrations"
export PYTHONPATH="/app${PYTHONPATH:+:${PYTHONPATH}}"
python scripts/run_migrations.py

echo "[entrypoint] starting application: $*"
exec "$@"

