"""Agent 分段 checkpoint：写入 session.config，支持触顶续跑与崩溃恢复提示。"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

CHECKPOINT_KEY = "_agent_checkpoint"


async def save_checkpoint(
    session_id: uuid.UUID,
    *,
    segment: int,
    iteration: int,
    mode: str,
    note: str = "",
    extra: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> None:
    try:
        from backend.repositories.session_repo import AsyncSessionRepository

        repo = AsyncSessionRepository()
        payload = {
            "segment": segment,
            "iteration": iteration,
            "mode": mode,
            "note": note,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        # Phase 0.5.2：checkpoint 挂到具体 run，resume 可溯源
        if run_id:
            payload["run_id"] = run_id
        if extra:
            payload["extra"] = extra
        # 键级合并：不再整体读改写 config，避免与 goal 等并发写互相覆盖
        await repo.merge_config_keys(session_id, {CHECKPOINT_KEY: payload})
        logger.info(
            "Checkpoint saved session=%s segment=%s iter=%s",
            str(session_id)[:8],
            segment,
            iteration,
        )
    except Exception as e:
        logger.warning("save_checkpoint failed: %s", e)


async def load_checkpoint(session_id: uuid.UUID) -> dict[str, Any] | None:
    try:
        from backend.repositories.session_repo import AsyncSessionRepository

        repo = AsyncSessionRepository()
        cfg = await repo.get_config(session_id) or {}
        raw = cfg.get(CHECKPOINT_KEY) if isinstance(cfg, dict) else None
        return raw if isinstance(raw, dict) else None
    except Exception as e:
        logger.warning("load_checkpoint failed: %s", e)
        return None


async def clear_checkpoint(session_id: uuid.UUID) -> None:
    try:
        from backend.repositories.session_repo import AsyncSessionRepository

        repo = AsyncSessionRepository()
        # 键级移除：只删 checkpoint 键，不碰其他调用方的键
        await repo.merge_config_keys(session_id, remove=[CHECKPOINT_KEY])
    except Exception as e:
        logger.warning("clear_checkpoint failed: %s", e)
