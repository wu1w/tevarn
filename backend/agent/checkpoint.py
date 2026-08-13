"""Agent 分段 checkpoint（Phase 2.3：权威落到 AgentRun.checkpoint，session 双写兼容）。"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

CHECKPOINT_KEY = "_agent_checkpoint"


def recorder_run_id(recorder: Any) -> str | None:
    """Run id from a RunRecorder — never treat a reasoning ``str`` as a recorder.

    Live bug: loop reused ``_rc`` for ``accumulated_reasoning``, then
    ``_rc.run_id`` at the next segment boundary raised
    ``'str' object has no attribute 'run_id'`` and aborted auto-continue setup.
    """
    if recorder is None or isinstance(recorder, (str, bytes, bytearray, int, float)):
        return None
    rid = getattr(recorder, "run_id", None)
    if rid is None:
        return None
    s = str(rid).strip()
    return s or None


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
    payload = {
        "segment": segment,
        "iteration": iteration,
        "mode": mode,
        "note": note,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if run_id:
        payload["run_id"] = run_id
    if extra:
        payload["extra"] = extra

    # Phase 2.3：Run 列为权威（有 run_id 时）
    if run_id:
        try:
            from backend.repositories.agent_run_repo import AsyncAgentRunRepository

            rid = uuid.UUID(str(run_id))
            await AsyncAgentRunRepository().update_run(rid, {"checkpoint": payload})
        except Exception as e:
            logger.warning("save_checkpoint to run failed: %s", e)

    # 双写 session.config：旧 resume / 无 run_id 路径仍可用
    try:
        from backend.repositories.session_repo import AsyncSessionRepository

        repo = AsyncSessionRepository()
        await repo.merge_config_keys(session_id, {CHECKPOINT_KEY: payload})
        logger.info(
            "Checkpoint saved session=%s segment=%s iter=%s run=%s",
            str(session_id)[:8],
            segment,
            iteration,
            (str(run_id)[:8] if run_id else "-"),
        )
    except Exception as e:
        logger.warning("save_checkpoint to session failed: %s", e)


async def load_checkpoint(
    session_id: uuid.UUID,
    *,
    run_id: str | uuid.UUID | None = None,
) -> dict[str, Any] | None:
    # 优先 Run 列（权威）
    rid = run_id
    if rid:
        try:
            from backend.repositories.agent_run_repo import AsyncAgentRunRepository

            run = await AsyncAgentRunRepository().get_run(
                rid if isinstance(rid, uuid.UUID) else uuid.UUID(str(rid))
            )
            if run is not None and isinstance(run.checkpoint, dict) and run.checkpoint:
                return dict(run.checkpoint)
        except Exception as e:
            logger.debug("load_checkpoint from run skipped: %s", e)

    try:
        from backend.repositories.session_repo import AsyncSessionRepository

        repo = AsyncSessionRepository()
        cfg = await repo.get_config(session_id) or {}
        raw = cfg.get(CHECKPOINT_KEY) if isinstance(cfg, dict) else None
        if isinstance(raw, dict) and raw:
            # 若 session 内嵌 run_id，再尝试读 Run 列更新版
            nested = raw.get("run_id")
            if nested and not rid:
                try:
                    from backend.repositories.agent_run_repo import (
                        AsyncAgentRunRepository,
                    )

                    run = await AsyncAgentRunRepository().get_run(uuid.UUID(str(nested)))
                    if run is not None and isinstance(run.checkpoint, dict) and run.checkpoint:
                        return dict(run.checkpoint)
                except Exception:
                    pass
            return raw
        return None
    except Exception as e:
        logger.warning("load_checkpoint failed: %s", e)
        return None


async def clear_checkpoint(
    session_id: uuid.UUID,
    *,
    run_id: str | uuid.UUID | None = None,
) -> None:
    if run_id:
        try:
            from backend.repositories.agent_run_repo import AsyncAgentRunRepository

            rid = run_id if isinstance(run_id, uuid.UUID) else uuid.UUID(str(run_id))
            await AsyncAgentRunRepository().update_run(rid, {"checkpoint": None})
        except Exception as e:
            logger.debug("clear_checkpoint on run skipped: %s", e)
    try:
        from backend.repositories.session_repo import AsyncSessionRepository

        repo = AsyncSessionRepository()
        await repo.merge_config_keys(session_id, remove=[CHECKPOINT_KEY])
    except Exception as e:
        logger.warning("clear_checkpoint failed: %s", e)
