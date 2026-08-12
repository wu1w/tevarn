#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== version sync =="
python scripts/sync_version.py --check

echo "== ruff =="
python -m ruff check backend

echo "== mypy (typed core sample) =="
python -m mypy backend/core/safe_subprocess.py --ignore-missing-imports

echo "== frontend typecheck =="
if [[ -d frontend/node_modules ]]; then
  (cd frontend && npm run typecheck)
else
  echo "skip typecheck (no node_modules)"
fi

echo "== frontend lint (zero errors; warnings allowed) =="
if [[ -d frontend/node_modules ]]; then
  (cd frontend && npx eslint . --max-warnings 9999)
else
  echo "skip lint (no node_modules)"
fi

echo "== pytest collect (must succeed) =="
python -m pytest backend/tests --co -q

echo "== smoke: no deleted API imports in tests =="
! rg -n "from backend.agent.run_events import .*_next_seq" backend/tests || {
  echo "FAIL: tests still import deleted _next_seq"
  exit 1
}

echo "gates OK"
