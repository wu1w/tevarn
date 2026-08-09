#!/usr/bin/env python3
"""Phase 5.1a：零外部依赖冒烟（不启 Redis/Qdrant）。

用法（项目 venv）:
  .venv/Scripts/python.exe scripts/smoke_zero_deps.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TEVARN_TEST_MODE", "1")
os.environ.setdefault("TEVARN_AGENT_DISPATCHER_ENABLED", "false")
os.environ.setdefault("TEVARN_AGENT_KERNEL_REDIS_SHARED", "false")
os.environ.setdefault("TEVARN_REDIS_URL", "")
os.environ.setdefault("TEVARN_QDRANT_URL", "")


def main() -> int:
    print(f"python={sys.executable}")
    from backend.core.config import settings
    from backend.core.version import product_version
    from backend.kernel.shared_store import create_shared_store_from_settings

    print(f"version={product_version()}")
    assert str(settings.db_url).startswith("sqlite"), settings.db_url
    assert settings.agent_kernel_redis_shared is False
    assert not (settings.redis_url or "").strip()
    assert not (settings.qdrant_url or "").strip()
    store = create_shared_store_from_settings()
    assert store is None, "redis shared should be off"
    # import app (no long-running servers)
    from backend.main import app

    print(f"app.title={app.title} app.version={app.version}")
    from backend.core.security_check import collect_security_report

    rep = collect_security_report()
    fails = [r for r in rep.results if r.level == "fail"]
    print(f"security_check worst={rep.worst} fails={len(fails)}")
    for f in fails:
        print(f"  FAIL {f.id}: {f.message}")
    if fails:
        return 1
    print("OK zero-deps smoke")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
