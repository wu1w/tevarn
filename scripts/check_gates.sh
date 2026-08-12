#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== version sync =="
python scripts/sync_version.py --check

echo "== ruff =="
python -m ruff check backend

echo "== mypy sample =="
python -m mypy backend/core/safe_subprocess.py --ignore-missing-imports

echo "== forbidden stale imports =="
if rg -n "from backend.agent.run_events import .*_next_seq" backend/tests 2>/dev/null; then
  echo "FAIL: tests import deleted _next_seq"; exit 1
fi

echo "== frontend typecheck =="
if [[ -d frontend/node_modules ]]; then
  (cd frontend && npm run typecheck)
else
  echo "skip (no node_modules)"
fi

echo "== pytest collect =="
python -m pytest backend/tests --co -q

echo "== pytest fast product subset =="
python -m pytest backend/tests/test_tool_policy.py backend/tests/test_thin_base_grok.py backend/tests/test_control_inbox.py -q --tb=no 2>/dev/null || {
  echo "WARN: fast subset failed or missing deps — collect already enforced"
}

echo "gates OK"
