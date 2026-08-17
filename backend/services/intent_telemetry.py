"""匿名化 Config Intent 遥测：只记意图类目与成败，不记密钥/原文。

写入本地 jsonl（用户数据目录），供产品反哺路由；默认开启、体积极小。
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAX_LINES = 2000


def _telemetry_path() -> Path:
    override = (os.environ.get("TEVARN_INTENT_TELEMETRY") or os.environ.get("TAKTON_INTENT_TELEMETRY") or "").strip()
    if override:
        return Path(override)
    try:
        from backend.core.config import get_tevarn_home
        return get_tevarn_home() / "intent_telemetry.jsonl"
    except Exception:
        return Path.home() / ".tevarn" / "intent_telemetry.jsonl"


def record_intent_event(
    *,
    kind: str,
    ok: bool,
    source: str = "config_intent",
    detail: str = "",
    duration_ms: int | None = None,
) -> None:
    """追加一条匿名事件。失败静默。"""
    try:
        path = _telemetry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        row: dict[str, Any] = {
            "ts": time.time(),
            "kind": str(kind or "unknown")[:64],
            "ok": bool(ok),
            "source": str(source or "")[:32],
        }
        if detail:
            # 禁止长原文
            row["detail"] = str(detail)[:120]
        if duration_ms is not None:
            row["duration_ms"] = int(duration_ms)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        # 粗暴截断：文件过大时保留尾部
        try:
            if path.stat().st_size > 512_000:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                if len(lines) > _MAX_LINES:
                    path.write_text(
                        "\n".join(lines[-_MAX_LINES:]) + "\n",
                        encoding="utf-8",
                    )
        except Exception:
            pass
    except Exception as e:
        logger.debug("intent telemetry skip: %s", e)


def summarize_recent(limit: int = 50) -> dict[str, Any]:
    """供调试/内部接口：按 kind 聚合最近 N 条。"""
    path = _telemetry_path()
    if not path.is_file():
        return {"path": str(path), "total": 0, "by_kind": {}}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, limit) :]
    except Exception:
        return {"path": str(path), "total": 0, "by_kind": {}}
    by: dict[str, dict[str, int]] = {}
    for line in lines:
        try:
            o = json.loads(line)
        except Exception:
            continue
        k = str(o.get("kind") or "?")
        b = by.setdefault(k, {"ok": 0, "fail": 0})
        if o.get("ok"):
            b["ok"] += 1
        else:
            b["fail"] += 1
    return {"path": str(path), "total": len(lines), "by_kind": by}
