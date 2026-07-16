"""Goal / 长任务自动续跑入口（可被 API 或 cron 调用）。"""
from __future__ import annotations

import logging
import uuid
from typing import Any

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
    """构造 NexusAgentLoop 并续跑。供 cron / 管理 API 使用。"""
    from backend.agent import NexusAgentLoop
    from backend.agent.checkpoint import load_checkpoint
    from backend.api.dependencies import (
        get_context_flow_repo,
        get_ctx_item_repo,
        get_message_repo,
        get_notification_repo,
        get_session_repo,
        get_task_repo,
    )

    sid = uuid.UUID(str(session_id)) if not isinstance(session_id, uuid.UUID) else session_id
    resume_prompt = prompt or await build_resume_prompt(sid)
    if not resume_prompt:
        return "[resume] nothing to resume"

    cp = await load_checkpoint(sid)
    run_mode = mode or (cp.get("mode") if cp else None) or "goal"

    uid = None
    if user_id is not None:
        uid = uuid.UUID(str(user_id)) if not isinstance(user_id, uuid.UUID) else user_id

    agent = NexusAgentLoop(
        session_repo=await get_session_repo(),
        message_repo=await get_message_repo(),
        task_repo=await get_task_repo(),
        ctx_item_repo=await get_ctx_item_repo(),
        context_flow_repo=await get_context_flow_repo(),
        ws_manager=None,
        user_id=uid,
        notification_repo=await get_notification_repo(),
    )
    logger.info("resume_session_agent session=%s mode=%s", str(sid)[:8], run_mode)
    return await agent.run(sid, resume_prompt, attachments=None, mode=run_mode)
