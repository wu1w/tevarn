#!/usr/bin/env bash
# Takton 后端启动脚本（dev/local）
#
# ⚠️  WORKERS 默认 1，不要随手调大：
#     kernel 的 escalations / processes / events 是进程内存态
#     （backend/kernel/kernel.py: self._escalations = {}）。
#     workers>1 时 approve 落在 worker A、list 落在 worker B，
#     内核语义分裂。仅当 kernel 状态外部化（DB/Redis）后才可调大。
set -euo pipefail
cd "$(dirname "$0")/.."

WORKERS="${TAKTON_UVICORN_WORKERS:-1}"
if [ "$WORKERS" != "1" ]; then
  echo "⚠️  TAKTON_UVICORN_WORKERS=$WORKERS —— kernel 内存态将在多 worker 间分裂，确认你知道在做什么" >&2
fi

exec .venv311/bin/python -m uvicorn backend.main:app \
  --host 127.0.0.1 --port 8090 --workers "$WORKERS"
