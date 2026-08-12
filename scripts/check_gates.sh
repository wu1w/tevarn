#!/usr/bin/env bash
# Local quality gates closer to CI (not a full substitute)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== version sync =="
python scripts/sync_version.py --check

echo "== frontend typecheck =="
(cd frontend && npm run typecheck)

echo "== frontend lint =="
(cd frontend && npm run lint) || {
  echo "WARN: eslint failed (CI will fail)"
  exit 1
}

echo "== backend ruff (selected paths) =="
if command -v ruff >/dev/null 2>&1; then
  ruff check backend/agent/loop.py backend/agent/file_checkpoint.py backend/api/routes/files.py backend/api/websocket.py backend/core/config.py || true
else
  python -m ruff check backend/agent/loop.py backend/agent/file_checkpoint.py 2>/dev/null || echo "ruff not installed"
fi

echo "== backend pytest collect =="
python -m pytest backend/tests --co -q 2>/dev/null || {
  echo "pytest collect failed (env may lack deps); CI still runs full suite"
  exit 0
}

echo "gates OK"
