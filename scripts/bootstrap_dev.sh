#!/usr/bin/env bash
# Phase 5.1b — 源码开发者 bootstrap（Linux/macOS）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
echo "[tevarn] root=$ROOT"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -U pip
if [[ -f pyproject.toml ]]; then
  .venv/bin/python -m pip install -e ".[dev]"
elif [[ -f backend/requirements.txt ]]; then
  .venv/bin/python -m pip install -r backend/requirements.txt
fi
if [[ -f frontend/package.json ]]; then
  (cd frontend && npm install)
fi
echo "[tevarn] bootstrap done. Next: .venv/bin/python start.py"
echo "[tevarn] version: $(tr -d '[:space:]' < backend/VERSION)"
