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
(cd frontend && npm run typecheck)

echo "== frontend lint (errors only; warnings allowed) =="
(cd frontend && npm run lint -- --max-warnings 9999)

echo "== pytest collect =="
python -m pytest backend/tests --co -q

echo "gates OK"
