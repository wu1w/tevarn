#!/usr/bin/env bash
# Local quality gates mirroring CI (frontend typecheck + backend pytest collect)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== version sync =="
python scripts/sync_version.py --check

echo "== frontend typecheck =="
(cd frontend && npm run typecheck)

echo "== backend pytest collect =="
python -m pytest backend/tests --co -q || {
  echo "pytest not fully available in this env; install requirements-dev.txt"
  exit 1
}

echo "gates OK"
