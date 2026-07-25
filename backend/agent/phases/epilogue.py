"""收尾聚合 epilogue（loop 拆分深化）

从 loop.py _run_locked 抽出的主循环后线性收尾块：
checkpoint 清理 → 多信源最终聚合 → TEE 进化钩子 → SFT 收集 →
最终回复持久化 → 透明化轨迹 → idle 状态推送 → 工作区复位。

行为冻结：tests/test_loop_freeze.py（拆分前后同绿）。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Callable

logger = logging.getLogger(__name__)


async def run_epilogue(
    loop: Any,
    *,
    session_id: uuid.UUID,
    final_content: str | None,
    goal_mode: bool,
    llm_service: Any,
    user_input: str,
    tool_rounds: int,
    last_tool_round_count: int,
    multi_source_pending: bool,
    suppress_content_stream: bool,
    sft_tools: list[dict[str, Any]],
    trace_start_time: float,
    global_iter: int,
    trace_thinking_steps: list[dict[str, Any]],
    trace_tool_calls: list[dict[str, Any]],
    trace_rag_sources: list[dict[str, Any]],
    ws_reset: Callable[[], None],
) -> str | None:
    """主循环后的收尾聚合；返回（可能被多信源聚合改写的）final_content"""

    # 正常结束则清理 checkpoint
    try:
        from backend.agent.checkpoint import clear_checkpoint
        from backend.agent.goal_state import get_goal

        g_done = get_goal(session_id) if goal_mode else None
        if not loop._should_stop and (not goal_mode or (g_done is None or g_done.is_complete())):
            await clear_checkpoint(session_id)
    except Exception:
        pass

    # 7.5 多信源最终聚合（额外一次无工具 LLM，避免「四个都对」并列）
    try:
        if final_content and not loop._should_stop:
            _before = final_content
            final_content = await loop._maybe_aggregate_multi_source(
                llm_service=llm_service,
                session_id=session_id,
                user_input=user_input,
                draft=final_content,
                tool_rounds=tool_rounds,
                last_tool_count=last_tool_round_count,
                multi_pending=multi_source_pending,
            )
            if final_content and (
                suppress_content_stream
                or final_content != _before
                or multi_source_pending
            ):
                try:
                    await loop._push_stream(session_id, uuid.uuid4(), final_content)
                except Exception as pe:
                    logger.debug("push aggregated stream skipped: %s", pe)
    except Exception as e:
        logger.warning("multi-source aggregate skipped: %s", e)

    # 7.6 TEE 自主进化：验收/归因/过门后 auto_apply（默认关总开关）
    try:
        from backend.evolution.config import get_evolution_config
        from backend.evolution.manager import get_evolution_manager

        if get_evolution_config().enabled and final_content and not loop._should_stop:
            await get_evolution_manager().on_turn_final(
                str(session_id),
                user_input=user_input or "",
                final_content=final_content or "",
            )
    except Exception as e:
        logger.warning("evolution turn hook skipped: %s", e)

    # 7.7 SFT / 使用日志（设置里开关，默认关）
    try:
        from backend.services.sft_collector import collect_if_enabled

        await collect_if_enabled(
            session_id=str(session_id),
            user_input=user_input or "",
            assistant_output=final_content or "",
            tools=list(sft_tools),
            meta={"source": "agent_loop"},
        )
    except Exception as e:
        logger.debug("sft collect skipped: %s", e)

    # 8. 保存最终回复 + 同步 CtxItem + 状态 + 通知（同一事务）
    try:
        await loop._persist_final_response(session_id, final_content)
    except Exception as e:
        logger.error(f"Failed to persist final response: {e}")
        # 兜底：至少把状态恢复为 idle
        try:
            await loop.session_repo.update_status(session_id, "idle")
        except Exception as status_err:
            logger.error(f"Failed to update session status: {status_err}")

    # 8.5 透明化轨迹持久化
    try:
        from backend.repositories.trace_repo import TraceRepository
        from backend.database import get_db_context

        _trace_duration = (__import__("time").monotonic() - trace_start_time) * 1000
        _iter_count = 0
        try:
            _iter_count = global_iter + 1
        except Exception:
            pass
        async with get_db_context() as db:
            trace_repo = TraceRepository(db)
            await trace_repo.create({
                "session_id": session_id,
                "user_id": loop.user_id,
                "thinking_steps": trace_thinking_steps,
                "tool_calls_trace": trace_tool_calls,
                "rag_sources": trace_rag_sources,
                "total_iterations": _iter_count,
                "total_tool_calls": len(trace_tool_calls),
                "duration_ms": _trace_duration,
                "user_input_summary": (user_input or "")[:200],
                "status": "completed",
            })
    except Exception as e:
        logger.debug("trace save skipped: %s", e)

    # 9. 推送状态为 idle（无论成功或失败都恢复状态）
    await loop._push_status(session_id, "idle", "Ready")

    logger.info(f"Agent loop completed for session {session_id}")
    try:
        ws_reset()
    except Exception:
        pass
    return final_content
