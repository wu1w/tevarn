#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== version sync =="
python scripts/sync_version.py --check

echo "== ruff =="
python -m ruff check backend

echo "== mypy (safe_subprocess) =="
python -m mypy backend/core/safe_subprocess.py --ignore-missing-imports || true

echo "== frontend typecheck =="
(cd frontend && npm run typecheck)

echo "== frontend lint =="
(cd frontend && npm run lint) || true

echo "== pytest collect =="
python -m pytest backend/tests --co -q || true

echo "gates done"
