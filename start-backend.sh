#!/usr/bin/env bash
# Tevarn backend (dev). Prefer: python start.py
# Port aligns with frontend rewrites / Electron (8090).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export PYTHONPATH="${PYTHONPATH:-$ROOT}"
export JWT_SECRET="${JWT_SECRET:-tevarn-dev-only-change-me}"
export API_KEY="${API_KEY:-tevarn-dev-api-key-change-me}"
export TEVARN_APP_PORT="${TEVARN_APP_PORT:-8090}"
PORT="${TEVARN_APP_PORT}"
exec python -m uvicorn backend.main:app --host 127.0.0.1 --port "$PORT" --reload
