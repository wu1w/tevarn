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
) -> None:
    try:
        from backend.repositories.session_repo import AsyncSessionRepository

        repo = AsyncSessionRepository()
        cfg = await repo.get_config(session_id) or {}
        if not isinstance(cfg, dict):
            cfg = {}
        cfg = dict(cfg)
        payload = {
            "segment": segment,
            "iteration": iteration,
            "mode": mode,
            "note": note,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if extra:
            payload["extra"] = extra
        cfg[CHECKPOINT_KEY] = payload
        await repo.update_config(session_id, cfg)
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
        cfg = await repo.get_config(session_id) or {}
        if isinstance(cfg, dict) and CHECKPOINT_KEY in cfg:
            cfg = dict(cfg)
            cfg.pop(CHECKPOINT_KEY, None)
            await repo.update_config(session_id, cfg)
    except Exception as e:
        logger.warning("clear_checkpoint failed: %s", e)
