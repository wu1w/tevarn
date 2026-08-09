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

    anchor = ""
    try:
        from backend.agent.progress_guard import resume_anchor_block
        from backend.tools.permissions import resolve_agent_workspace_root

        anchor = resume_anchor_block(resolve_agent_workspace_root() or "")
    except Exception:
        anchor = ""

    if g is not None and not g.is_complete():
        return (
            "[System auto-resume] Continue the unfinished Goal; do not redo completed steps.\n"
            "If the previous segment paused on tool-round cap, counters are reset — "
            "work directly; do not restate force-final/budget/done-list essays.\n"
            + (anchor + "\n" if anchor else "")
            + "Call manage_goal(action=get) for progress, then execute remaining todos.\n"
            "Do not text-only finish while incomplete; avoid disk scans/where/rustup/_diag.\n"
            "Reply to the user in their language.\n\n"
            + g.summary_for_llm()
        )

    if cp:
        note = str(cp.get("note") or "")
        # Soft segment wording — never "token budget exhausted"
        pause_why = "segment tool-round cap"
        if "token" in note.lower() or "budget_ratio" in note:
            pause_why = "process token pressure"
        return (
            f"[System auto-resume] Previous run paused ({pause_why}). "
            f"segment={cp.get('segment')} iteration={cp.get('iteration')} mode={cp.get('mode')}.\n"
            + (anchor + "\n" if anchor else "")
            + "Continue from the checkpoint; avoid redoing finished work; "
            "no force-final report replay. Reply to the user in their language.\n"
            + (f"Note: {note}" if note else "")
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
    try:
        out = await agent.run(sid, resume_prompt, attachments=None, mode=run_mode)
    except Exception:
        # 续跑异常：将会话内仍非终态的旧 run 标 failed，避免永久 hung
        await _mark_session_interrupted_terminal(
            sid, status="failed", reason="resume_exception"
        )
        raise
    # 续跑成功：将会话内 interrupted 旧 run 收口为 done（新 run 由 recorder 自己收尾）
    await _mark_session_interrupted_terminal(
        sid, status="done", reason="resume_ok"
    )
    return out


async def _mark_session_interrupted_terminal(
    session_id: uuid.UUID,
    *,
    status: str = "done",
    reason: str = "resume",
) -> int:
    """把会话下仍为 interrupted/executing(recovered) 的旧 AgentRun 推到终态。"""
    n = 0
    try:
        from datetime import datetime, timezone

        from backend.agent.run_state import TERMINAL_STATES, RunStatus
        from backend.repositories.agent_run_repo import AsyncAgentRunRepository

        repo = AsyncAgentRunRepository()
        runs = await repo.list_runs(session_id, limit=20)
        terminal = {s.value for s in TERMINAL_STATES}
        want_fail = status == "failed"
        for run in runs:
            st = str(getattr(run, "status", "") or "")
            if st in terminal:
                continue
            # 只收口 interrupted，以及 recovery 拨到 executing 但未结束的
            meta = getattr(run, "meta", None) or {}
            recovered = isinstance(meta, dict) and meta.get("recovered_from") == "interrupted"
            if st not in (RunStatus.INTERRUPTED.value, RunStatus.EXECUTING.value) and not recovered:
                # 仍允许收口 interrupted 别名
                if st != "interrupted":
                    continue
            if st == RunStatus.EXECUTING.value and not recovered:
                # 可能是本次 resume 新建的 live run——跳过（由 recorder 收尾）
                continue
            new_status = (
                RunStatus.FAILED.value if want_fail else RunStatus.DONE.value
            )
            try:
                await repo.update_run(
                    run.id,
                    {
                        "status": new_status,
                        "meta": {
                            **(meta if isinstance(meta, dict) else {}),
                            "terminal_via": reason,
                            "terminal_at": datetime.now(timezone.utc).isoformat(),
                        },
                    },
                )
                n += 1
            except Exception as e:
                logger.debug("mark terminal skip run=%s: %s", getattr(run, "id", "?"), e)
    except Exception as e:
        logger.debug("mark session interrupted terminal: %s", e)
    return n


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
