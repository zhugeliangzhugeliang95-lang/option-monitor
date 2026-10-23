#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -d .venv ]; then python3 -m venv .venv; fi
if ! .venv/bin/python -c 'import fastapi, statsmodels, sqlalchemy, dotenv, futu' 2>/dev/null; then .venv/bin/python -m pip install -r backend/requirements.lock; fi
if [ ! -d frontend/node_modules ]; then (cd frontend && npm ci); fi
exec .venv/bin/python scripts/dev.py "$@"
