#!/usr/bin/env bash
# Build Linux desktop packages (AppImage + deb) with bundled backend/.venv
# Usage (repo root):
#   bash scripts/build-linux-desktop.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -x .venv/bin/python ]]; then
  echo "[tevarn] creating .venv (required for packaging backend deps)..."
  python3 -m venv --copies .venv
fi

echo "[tevarn] ensuring prod deps in .venv..."
.venv/bin/pip install -U pip setuptools wheel >/dev/null
.venv/bin/pip install -r backend/requirements-prod.txt
.venv/bin/python -c "import uvicorn,fastapi,mcp; print('[tevarn] venv ok')"

cd frontend
if [[ ! -d node_modules/electron-builder ]]; then
  npm install --no-fund --no-audit
fi

export CSC_IDENTITY_AUTO_DISCOVERY=false
npm run dist:linux

echo "[tevarn] artifacts:"
ls -lah release/Tevarn-*.AppImage release/tevarn_*.deb 2>/dev/null || ls -lah release/
if [[ -x release/linux-unpacked/resources/backend/.venv/bin/python ]]; then
  echo "[tevarn] OK packaged venv present"
else
  echo "[tevarn] ERROR: packaged backend/.venv missing" >&2
  exit 1
fi
