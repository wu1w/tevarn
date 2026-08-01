"""Goal / 长任务自动续跑入口（可被 API 或 cron 调用）。"""
from __future__ import annotations

import logging
import uuid

logger = logging.getLogger(__name__)


async def build_resume_prompt(session_id: uuid.UUID | str) -> str | None:
    """若 goal 未完成或存在 checkpoint，生成续跑提示词；否则 None。"""
    from backend.agent.checkpoint import load_checkpoint
    from backend.agent.goal_state import get_goal, load_goal_from_db

    sid = uuid.UUID(str(session_id)) if not isinstance(session_id, uuid.UUID) else session_id
    await load_goal_from_db(sid)
    g = get_goal(sid)
    cp = await load_checkpoint(sid)

    if g is not None and not g.is_complete():
        return (
            "【系统自动续跑】请继续推进未完成的 Goal，不要重复已完成步骤。"
            "先用 manage_goal(action=get) 确认进度，再执行剩余 todo。\n\n"
            + g.summary_for_llm()
        )

    if cp:
        return (
            "【系统自动续跑】上一轮因轮次上限暂停。"
            f"segment={cp.get('segment')} iteration={cp.get('iteration')} mode={cp.get('mode')}。"
            "请从断点继续完成任务，避免重复已完成工作。"
            + (f"\n备注: {cp.get('note')}" if cp.get("note") else "")
        )
    return None


async def resume_session_agent(
    session_id: uuid.UUID | str,
    *,
    user_id: uuid.UUID | str | None = None,
    mode: str | None = None,
    prompt: str | None = None,
) -> str:
    """构造 NexusAgentLoop 并续跑。供 cron / 管理 API 使用。

    必须挂上 ws_manager，否则聊天侧看不到流式/工具事件，表现为「卡住」。
    """
    from backend.agent import NexusAgentLoop
    from backend.agent.checkpoint import load_checkpoint
    from backend.kernel.ports import get_ws_manager
    from backend.repositories.context_repo import (
        AsyncContextFlowRepository,
        AsyncCtxItemRepository,
    )
    from backend.repositories.message_repo import AsyncMessageRepository
    from backend.repositories.notification_repo import AsyncNotificationRepository
    from backend.repositories.session_repo import AsyncSessionRepository
    from backend.repositories.task_repo import AsyncTaskRepository

    sid = uuid.UUID(str(session_id)) if not isinstance(session_id, uuid.UUID) else session_id
    resume_prompt = prompt or await build_resume_prompt(sid)
    if not resume_prompt:
        return "[resume] nothing to resume"

    cp = await load_checkpoint(sid)
    run_mode = mode or (cp.get("mode") if cp else None) or "goal"

    uid = None
    if user_id is not None:
        uid = uuid.UUID(str(user_id)) if not isinstance(user_id, uuid.UUID) else user_id

    # Restore contact/steward from session config so budget + tools match chat path
    contact = ""
    try:
        sess = await AsyncSessionRepository().get_by_id(sid)
        cfg = getattr(sess, "config", None) or {}
        if isinstance(cfg, dict):
            contact = str(cfg.get("contact_agent") or "").strip()
    except Exception:
        contact = ""

    agent = NexusAgentLoop(
        session_repo=AsyncSessionRepository(),
        message_repo=AsyncMessageRepository(),
        task_repo=AsyncTaskRepository(),
        ctx_item_repo=AsyncCtxItemRepository(),
        context_flow_repo=AsyncContextFlowRepository(),
        ws_manager=get_ws_manager(),
        user_id=uid,
        notification_repo=AsyncNotificationRepository(),
    )
    if contact:
        agent._contact_agent = contact  # type: ignore[attr-defined]
    logger.info(
        "resume_session_agent session=%s mode=%s contact=%s",
        str(sid)[:8],
        run_mode,
        contact or "-",
    )
    return await agent.run(sid, resume_prompt, attachments=None, mode=run_mode)


async def resume_session_agent_background(
    session_id: uuid.UUID | str,
    *,
    user_id: uuid.UUID | str | None = None,
    mode: str | None = None,
    prompt: str | None = None,
) -> None:
    """Fire-and-forget；接入 WS track/snapshot，使 stop/sync 可见（P1）。"""
    import asyncio
    import uuid as _uuid

    sid = (
        session_id
        if isinstance(session_id, _uuid.UUID)
        else _uuid.UUID(str(session_id))
    )
    manager = None
    try:
        from backend.api.websocket import manager as ws_manager

        manager = ws_manager
    except Exception:
        manager = None

    async def _run() -> None:
        try:
            if manager is not None:
                try:
                    manager.begin_run_snapshot(sid)
                except Exception:
                    pass
            out = await resume_session_agent(
                session_id, user_id=user_id, mode=mode, prompt=prompt
            )
            logger.info(
                "resume background done session=%s preview=%s",
                str(session_id)[:8],
                (out or "")[:120],
            )
        except Exception as e:
            logger.exception(
                "resume background failed session=%s: %s", str(session_id)[:8], e
            )
        finally:
            if manager is not None:
                try:
                    manager.end_run_snapshot(sid)
                except Exception:
                    pass

    try:
        task = asyncio.create_task(_run(), name=f"resume:{str(sid)[:8]}")
        if manager is not None and hasattr(manager, "track_agent_task"):
            # loop 在 resume 内新建，stop 至少能 cancel task
            manager.track_agent_task(sid, task, loop=None)
    except Exception as e:
        logger.exception("resume background schedule failed: %s", e)
        await _run()
