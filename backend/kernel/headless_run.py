"""Headless single-prompt runner for CI / scripts (JSON-friendly)."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)


async def run_headless(
    prompt: str,
    *,
    user_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    identity_id: str | None = None,
    always_approve: bool = False,
    max_iterations: int | None = None,
    mode: str = "headless",
) -> dict[str, Any]:
    """Run one agent turn and return structured JSON (no WebSocket required)."""
    started = time.time()
    text_prompt = (prompt or "").strip()
    if not text_prompt:
        return {"ok": False, "error": "empty prompt", "text": ""}

    from backend.agent import NexusAgentLoop
    from backend.repositories.context_repo import (
        AsyncContextFlowRepository,
        AsyncCtxItemRepository,
    )
    from backend.repositories.message_repo import AsyncMessageRepository
    from backend.repositories.notification_repo import AsyncNotificationRepository
    from backend.repositories.session_repo import AsyncSessionRepository
    from backend.repositories.task_repo import AsyncTaskRepository

    sid = session_id
    uid = user_id
    if uid is None:
        # single-user / CI: first active user
        try:
            from sqlalchemy import select

            from backend.database import AsyncSessionLocal
            from backend.models.user import User

            async with AsyncSessionLocal() as db:
                uid = (
                    await db.execute(select(User.id).order_by(User.created_at.asc()).limit(1))
                ).scalar_one_or_none()
        except Exception:
            uid = None
    if uid is None:
        return {"ok": False, "error": "user_id required (no users in DB)", "text": ""}

    if sid is None:
        from backend.database import AsyncSessionLocal
        from backend.models.session import Session

        async with AsyncSessionLocal() as db:
            row = Session(
                user_id=uid,
                config={"source": "headless", "always_approve": always_approve},
            )
            db.add(row)
            await db.commit()
            await db.refresh(row)
            sid = row.id

    loop = NexusAgentLoop(
        session_repo=AsyncSessionRepository(),
        message_repo=AsyncMessageRepository(),
        task_repo=AsyncTaskRepository(),
        ctx_item_repo=AsyncCtxItemRepository(),
        context_flow_repo=AsyncContextFlowRepository(),
        ws_manager=None,
        user_id=uid,
        notification_repo=AsyncNotificationRepository(),
    )
    try:
        from backend.core.config import settings as _st_h
        _h_cap = int(getattr(_st_h, "agent_headless_max_iterations", 12) or 12)
    except Exception:
        _h_cap = 12
    try:
        if max_iterations:
            loop.max_iterations = max(1, int(max_iterations))
        else:
            loop.max_iterations = max(1, min(int(loop.max_iterations or _h_cap), _h_cap))
    except Exception:
        try:
            loop.max_iterations = _h_cap
        except Exception:
            pass
    try:
        loop._headless_run = True
        loop._thrash_force_final_override = True
    except Exception:
        pass
    if always_approve:
        # soft hint — permission headless still applies secrets deny
        try:
            from backend.core.config import settings

            settings.agent_permission_ask_mode = "local_allow"  # type: ignore[attr-defined]
        except Exception:
            pass
    if identity_id:
        loop._agent_key = f"wf:{identity_id}"  # type: ignore[attr-defined]
        loop._agent_label = str(identity_id)[:8]  # type: ignore[attr-defined]

    try:
        result = await loop.run(sid, text_prompt, attachments=None, mode=mode)
        text = result if isinstance(result, str) else str(result or "")
        return {
            "ok": True,
            "text": text,
            "session_id": str(sid),
            "identity_id": identity_id,
            "duration_ms": int((time.time() - started) * 1000),
        }
    except Exception as e:
        logger.exception("headless run failed")
        return {
            "ok": False,
            "error": str(e),
            "text": "",
            "session_id": str(sid) if sid else None,
            "duration_ms": int((time.time() - started) * 1000),
        }
