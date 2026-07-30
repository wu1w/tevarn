"""Phase 2.3：启动时 Run 恢复（kill -9 / 崩溃后）。

流程：
1. 扫描非终态 AgentRun → status=interrupted（诚实标记）
2. 若 agent_run_auto_recover：
   - origin in (inbox, cron, headless)：自动 resume_session_agent
   - origin == chat / subagent / cluster：只标记 interrupted，等用户/父流程续跑
3. 续跑使用 resume.py 提示词，不重复已完成步骤（依赖 checkpoint/goal）

见 docs/design/RUN_UNIFICATION.md § Durable。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# 自动续跑的 origin（有后台执行语义、无需用户坐在聊天框前）
_AUTO_RESUME_ORIGINS = frozenset({"inbox", "cron", "headless"})


def _auto_recover_enabled() -> bool:
    try:
        from backend.core.config import settings

        return bool(getattr(settings, "agent_run_auto_recover", True))
    except Exception:
        return True


async def recover_stale_runs(*, auto_resume: bool | None = None) -> dict[str, Any]:
    """启动恢复入口。返回统计摘要。"""
    from backend.agent.run_state import RunStatus
    from backend.repositories.agent_run_repo import AsyncAgentRunRepository

    do_resume = _auto_recover_enabled() if auto_resume is None else bool(auto_resume)
    repo = AsyncAgentRunRepository()

    # 1) 非终态 → interrupted
    n_mark = 0
    try:
        n_mark = await repo.mark_interrupted_nonterminal()
    except Exception as e:
        logger.warning("mark_interrupted_nonterminal failed: %s", e)

    interrupted: list[Any] = []
    try:
        interrupted = await repo.list_recent(limit=200, status=RunStatus.INTERRUPTED.value)
    except Exception as e:
        logger.warning("list interrupted runs failed: %s", e)

    summary: dict[str, Any] = {
        "marked_interrupted": n_mark,
        "interrupted_seen": len(interrupted),
        "auto_resume_enabled": do_resume,
        "resumed": 0,
        "resume_errors": 0,
        "skipped_chat": 0,
        "details": [],
    }

    if not do_resume or not interrupted:
        logger.info(
            "run recovery: marked=%s interrupted=%s auto_resume=%s",
            n_mark,
            len(interrupted),
            do_resume,
        )
        return summary

    # 2) 按 origin 策略续跑（串行，避免启动风暴）
    for run in interrupted:
        origin = str(getattr(run, "origin", "") or "chat")
        detail = {
            "run_id": str(run.id),
            "origin": origin,
            "session_id": str(run.session_id) if run.session_id else None,
        }
        if origin not in _AUTO_RESUME_ORIGINS:
            summary["skipped_chat"] += 1
            detail["action"] = "leave_interrupted"
            summary["details"].append(detail)
            continue
        if not run.session_id:
            detail["action"] = "skip_no_session"
            summary["details"].append(detail)
            continue
        try:
            # 先把状态拨到 executing，再 resume（lifecycle 允许 interrupted→executing）
            await repo.update_run(
                run.id,
                {
                    "status": RunStatus.EXECUTING.value,
                    "meta": {
                        **(run.meta or {}),
                        "recovered_at": datetime.now(timezone.utc).isoformat(),
                        "recovered_from": "interrupted",
                    },
                },
            )
            from backend.agent.resume import resume_session_agent

            mode = str(getattr(run, "mode", None) or "default")
            # workforce 续跑保持 workforce mode
            if origin in ("inbox", "cron"):
                mode = "workforce"
            text = await resume_session_agent(
                run.session_id,
                user_id=getattr(run, "user_id", None),
                mode=mode,
            )
            detail["action"] = "resumed"
            detail["result_preview"] = (text or "")[:120]
            summary["resumed"] += 1
        except Exception as e:
            logger.warning(
                "auto-resume failed run=%s: %s",
                str(run.id)[:8],
                e,
            )
            summary["resume_errors"] += 1
            detail["action"] = "resume_error"
            detail["error"] = str(e)[:200]
            try:
                await repo.update_run(
                    run.id,
                    {
                        "status": RunStatus.INTERRUPTED.value,
                        "error": f"auto-resume failed: {e}"[:500],
                    },
                )
            except Exception:
                pass
        summary["details"].append(detail)
        # 轻微让出事件循环
        await asyncio.sleep(0)

    logger.info(
        "run recovery done: marked=%s resumed=%s errors=%s left_chat=%s",
        n_mark,
        summary["resumed"],
        summary["resume_errors"],
        summary["skipped_chat"],
    )
    return summary
