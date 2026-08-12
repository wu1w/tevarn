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

    # P1 coding delivery: emit structured brief to FE
    try:
        from backend.agent.coding_loop import (
            drop_coding_loop,
            mark_deliver,
            phase_label,
        )
        from backend.agent.run_brief import drop_brief, get_brief
        from backend.agent.run_events import emit_run_event

        cl = mark_deliver(session_id)
        brief = get_brief(session_id)
        delivery = brief.delivery_payload()
        if delivery is not None or (cl.active and cl.phase.value != "idle"):
            delivery = delivery or {}
            delivery["phase"] = cl.phase.value if cl.active else delivery.get("phase")
            delivery["phase_label"] = phase_label(cl.phase) if cl.active else ""
            delivery["coding_loop"] = cl.to_dict() if cl.active else None
            await emit_run_event(
                getattr(loop, "ws_manager", None),
                session_id,
                "coding.delivery",
                detail=f"phase={delivery.get('phase')} files={len(delivery.get('changed_files') or [])} tests={len(delivery.get('tests') or [])}",
                payload=delivery,
            )
            if cl.active:
                await emit_run_event(
                    getattr(loop, "ws_manager", None),
                    session_id,
                    "coding.phase",
                    detail=phase_label(cl.phase),
                    payload={"phase": cl.phase.value, "active": cl.active},
                )
        # Terminal event: only if RunRecorder has not already published
        _rec = getattr(loop, "_run_recorder", None)
        _already = bool(
            getattr(loop, "_terminal_event_emitted", False)
            or (
                _rec is not None
                and str(getattr(_rec, "_status", "") or "").lower()
                in ("done", "failed", "cancelled", "interrupted")
            )
        )
        if not _already:
            _gen = None
            try:
                _gen = int(getattr(loop, "_run_generation", None) or 0) or None
            except Exception:
                _gen = None
            await emit_run_event(
                getattr(loop, "ws_manager", None),
                session_id,
                "run.completed" if not loop._should_stop else "run.cancelled",
                detail=(final_content or "")[:120],
                run_id=str(getattr(_rec, "run_id", "") or "") or None,
                generation=_gen,
            )
            try:
                loop._terminal_event_emitted = True
            except Exception:
                pass
        # Always drop ephemeral run state (stop/error/success)
        try:
            drop_brief(session_id)
            drop_coding_loop(session_id)
        except Exception:
            pass
    except Exception as _del_e:
        logger.debug("coding delivery emit skip: %s", _del_e)
        try:
            from backend.agent.coding_loop import drop_coding_loop
            from backend.agent.run_brief import drop_brief
            drop_brief(session_id)
            drop_coding_loop(session_id)
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

    # 7.55 多类目落地校验脚注（审计路径 / 检索无来源 / 统计无计算工具…）
    try:
        if final_content and not loop._should_stop:
            from backend.agent.task_grounding import maybe_annotate_report

            tools_for_gate: list[str] = []
            for tc in trace_tool_calls or []:
                if isinstance(tc, dict) and tc.get("name"):
                    tools_for_gate.append(str(tc["name"]))
            for st in sft_tools or []:
                if isinstance(st, dict) and st.get("name"):
                    tools_for_gate.append(str(st["name"]))
            annotated = maybe_annotate_report(
                user_input or "",
                final_content,
                tools_for_gate,
            )
            if annotated and annotated != final_content:
                final_content = annotated
                logger.info(
                    "task grounding footer attached session=%s",
                    str(session_id)[:8],
                )
    except Exception as e:
        logger.debug("task grounding annotate skipped: %s", e)

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

    # 7.65 轨迹蒸馏 + 技能计分（P1 2026-07-29）：成功经验也沉淀；
    # 产物只进 draft 审批链，进化技能被用到则记 outcome + 退化检查
    try:
        from backend.evolution.config import get_evolution_config as _gec

        if _gec().enabled and not loop._should_stop:
            _success = bool(final_content)
            _trace = [tc for tc in (trace_tool_calls or []) if isinstance(tc, dict)]

            from backend.evolution.distiller import distill_from_trajectory

            await distill_from_trajectory(
                user_input=user_input or "",
                tool_trace=_trace,
                final_content=final_content or "",
                success=_success,
                session_id=str(session_id),
            )

            # 本轮用到的进化技能：记分 + 退化自动回滚检查
            from backend.evolution.scoreboard import maybe_rollback, record_outcome
            from backend.tools.base import ToolSource as _TS
            from backend.tools.registry import ToolRegistry as _TR

            _seen: set[str] = set()
            for tc in _trace:
                _n = str(tc.get("name") or "")
                if not _n or _n in _seen:
                    continue
                _seen.add(_n)
                _t = _TR.get(_n)
                if _t is None or getattr(_t, "source", None) != _TS.DYNAMIC:
                    continue
                record_outcome(
                    skill_name=_n, success=_success, session_id=str(session_id)
                )
                await maybe_rollback(_n)
    except Exception as e:
        logger.debug("distill/scoreboard hook skipped: %s", e)

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

    # 8. 保存最终回复前：折叠 force_final 恐吓长文；空/短 handoff 合成用户向小结
    try:
        from backend.agent.thinking_format import (
            ensure_user_facing_final,
            looks_like_force_final_report,
            sanitize_force_final_body,
        )

        if looks_like_force_final_report(final_content) or (
            goal_mode
            and final_content
            and len(final_content) > 2500
            and (
                "强制收束" in final_content
                or "预算将尽" in final_content
                or "工具轮次已达" in final_content
                or "force-final" in final_content.lower()
                or "Segment tool rounds exhausted" in final_content
                or "Token budget low" in final_content
            )
        ):
            # Never prefer_short=True for goal — that wiped real summaries
            final_content = sanitize_force_final_body(
                final_content,
                goal_mode=bool(goal_mode),
                prefer_short=False,
            )
        # Empty / stock「工具轮次已用尽」→ synthesize from transcript + active goal only
        _gsum = ""
        try:
            from backend.agent.goal_state import get_goal

            _g = get_goal(session_id)
            if (
                goal_mode
                and _g is not None
                and not _g.is_complete()
                and str(getattr(_g, "status", "") or "") == "active"
            ):
                _gsum = _g.summary_for_llm()
        except Exception:
            pass
        _msgs = getattr(loop, "_last_messages_for_summary", None)
        final_content = ensure_user_facing_final(
            final_content,
            user_input=user_input or "",
            messages=_msgs if isinstance(_msgs, list) else None,
            exit_reason=str(getattr(loop, "last_exit_reason", "") or ""),
            goal_summary=_gsum,
            tool_rounds=int(tool_rounds or 0),
            goal_mode=bool(goal_mode),
        )
    except Exception as _san_e:
        logger.debug("force_final sanitize skip: %s", _san_e)

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
        from backend.database import get_db_context
        from backend.repositories.trace_repo import TraceRepository

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

    # 8.6 Auto-remember high-signal decisions (memory_nodes was always 0)
    try:
        from backend.agent.auto_remember import maybe_auto_remember

        _rid = None
        try:
            _rc = getattr(loop, "_run_recorder", None)
            _rid = getattr(_rc, "run_id", None) if _rc else None
        except Exception:
            pass
        await maybe_auto_remember(
            user_input=user_input or "",
            final_content=final_content or "",
            user_id=getattr(loop, "user_id", None),
            session_id=session_id,
            run_id=_rid,
        )
    except Exception as e:
        logger.debug("auto_remember skipped: %s", e)

    # 9. 推送状态为 idle（无论成功或失败都恢复状态）
    await loop._push_status(session_id, "idle", "Ready")

    logger.info(f"Agent loop completed for session {session_id}")
    try:
        ws_reset()
    except Exception:
        pass

    # 9.5 Finalize orphan executing/planning runs (session idle residual)
    try:
        from backend.core.config import settings as _st_or

        if bool(getattr(_st_or, "agent_finalize_orphan_runs_on_idle", True)):
            await _finalize_orphan_runs(loop, session_id=session_id)
    except Exception as _or_e:
        logger.debug("finalize orphan runs skip: %s", _or_e)

    # 10. Goal 未完成且非用户停止 → 自动再开一轮 resume（防 text-only 假完成）
    try:
        if not loop._should_stop:
            await _maybe_auto_resume_incomplete_goal(
                loop, session_id=session_id, user_input=user_input or ""
            )
    except Exception as _ar_e:
        logger.debug("goal incomplete auto-resume schedule skip: %s", _ar_e)

    return final_content


async def _finalize_orphan_runs(loop: Any, *, session_id: uuid.UUID) -> None:
    """Mark stale executing/planning runs terminal when session goes idle.

    Backfill total_tool_calls / total_iterations from run_steps when possible
    so UI does not show zeros after orphan close.
    """
    from datetime import datetime, timezone

    from backend.repositories.agent_run_repo import AsyncAgentRunRepository

    current_id = None
    try:
        _rc = getattr(loop, "_run_recorder", None)
        current_id = getattr(_rc, "run_id", None) if _rc else None
        # Flush live counters before idle (session may look idle while finishing)
        if _rc is not None and hasattr(_rc, "_flush_counters_safe"):
            await _rc._flush_counters_safe()
    except Exception:
        current_id = None

    repo = AsyncAgentRunRepository()
    runs = await repo.list_runs(session_id, limit=30)
    n = 0
    for run in runs or []:
        st = str(getattr(run, "status", "") or "")
        if st not in ("executing", "planning", "interrupted"):
            continue
        rid = getattr(run, "id", None)
        if current_id and rid and str(rid).replace("-", "") == str(current_id).replace(
            "-", ""
        ):
            # current run — recorder owns terminal transition
            continue
        meta = getattr(run, "meta", None) or {}
        if not isinstance(meta, dict):
            meta = {}
        # Backfill counters from steps if totals empty
        tools_n = int(getattr(run, "total_tool_calls", 0) or 0)
        iters_n = int(getattr(run, "total_iterations", 0) or 0)
        try:
            if tools_n == 0 and hasattr(repo, "count_steps"):
                tools_n = int(await repo.count_steps(rid, kind="tool") or 0)
            elif tools_n == 0 and hasattr(repo, "list_steps"):
                steps = await repo.list_steps(rid, limit=500)
                tools_n = sum(
                    1
                    for s in (steps or [])
                    if str(getattr(s, "kind", "") or "") == "tool"
                )
        except Exception:
            pass
        try:
            if iters_n == 0:
                cp = getattr(run, "checkpoint", None)
                if isinstance(cp, str) and cp.strip().startswith("{"):
                    import json

                    cp = json.loads(cp)
                if isinstance(cp, dict):
                    iters_n = int(cp.get("iteration") or 0)
        except Exception:
            pass
        try:
            data: dict[str, Any] = {
                "status": "done",
                "ended_at": datetime.now(timezone.utc),
                "meta": {
                    **meta,
                    "terminal_via": "session_idle_orphan_finalize",
                    "terminal_at": datetime.now(timezone.utc).isoformat(),
                },
            }
            if tools_n:
                data["total_tool_calls"] = tools_n
            if iters_n:
                data["total_iterations"] = iters_n
            await repo.update_run(rid, data)
            n += 1
        except Exception as e:
            logger.debug("orphan finalize skip %s: %s", rid, e)
    if n:
        logger.info(
            "finalized %s orphan runs for session=%s", n, str(session_id)[:8]
        )


async def _maybe_auto_resume_incomplete_goal(
    loop: Any,
    *,
    session_id: uuid.UUID,
    user_input: str,
) -> None:
    """If session still has an active incomplete Goal, fire one more resume run."""
    from backend.core.config import settings

    if not bool(getattr(settings, "agent_goal_incomplete_auto_resume", True)):
        return
    # Skip auto-resume after thrash/doom — avoids empty segment chains
    try:
        if bool(getattr(settings, "agent_no_autoresume_on_thrash", True)):
            from backend.agent.goal_facade import is_thrash_exit_reason

            _ex = str(getattr(loop, "last_exit_reason", "") or "").lower()
            if is_thrash_exit_reason(_ex):
                logger.info(
                    "skip goal auto-resume (thrash exit=%s) session=%s",
                    _ex,
                    str(session_id)[:8],
                )
                try:
                    await loop._push_status(
                        session_id,
                        "thinking",
                        "因工具空转熔断，已停止自动续跑；请手动发送「请继续」。",
                    )
                except Exception:
                    pass
                return
    except Exception:
        pass
    from backend.agent.goal_state import get_goal, load_goal_from_db

    await load_goal_from_db(session_id)
    g = get_goal(session_id)
    if g is None or g.is_complete():
        return
    if str(getattr(g, "status", "") or "") not in ("active", "blocked"):
        return

    max_chain = max(
        1, int(getattr(settings, "agent_goal_incomplete_auto_resume_max", 8) or 8)
    )
    # Persist chain count on checkpoint extra
    chain = 0
    try:
        from backend.agent.checkpoint import load_checkpoint, save_checkpoint

        cp = await load_checkpoint(session_id) or {}
        extra = cp.get("extra") if isinstance(cp.get("extra"), dict) else {}
        chain = int(extra.get("goal_auto_resume_chain") or 0)
        if chain >= max_chain:
            logger.warning(
                "goal auto-resume chain cap %s reached session=%s — stop chaining",
                max_chain,
                str(session_id)[:8],
            )
            await loop._push_status(
                session_id,
                "thinking",
                f"Goal 仍未完成，但已自动续跑 {max_chain} 轮；请手动发送「请继续」。",
            )
            return
        chain += 1
        await save_checkpoint(
            session_id,
            segment=int(cp.get("segment") or 0),
            iteration=int(cp.get("iteration") or 0),
            mode=str(cp.get("mode") or "goal"),
            note="goal_incomplete_auto_resume",
            extra={**extra, "goal_auto_resume_chain": chain},
            run_id=cp.get("run_id"),
        )
    except Exception as e:
        logger.debug("goal auto-resume chain track: %s", e)
        chain = 1

    uid = getattr(loop, "user_id", None)
    logger.info(
        "scheduling goal incomplete auto-resume chain=%s/%s session=%s",
        chain,
        max_chain,
        str(session_id)[:8],
    )
    try:
        await loop._push_status(
            session_id,
            "thinking",
            f"Goal 未完成，自动再续一轮（{chain}/{max_chain}）…",
        )
    except Exception:
        pass

    # Delay slightly so current run fully tears down (WS idle / locks)
    import asyncio

    async def _delayed() -> None:
        try:
            await asyncio.sleep(1.2)
            from backend.agent.resume import resume_session_agent_background

            await resume_session_agent_background(
                session_id,
                user_id=uid,
                mode="goal",
            )
        except Exception as e:
            logger.warning(
                "goal incomplete auto-resume failed session=%s: %s",
                str(session_id)[:8],
                e,
            )

    try:
        asyncio.create_task(
            _delayed(), name=f"goal-auto-resume:{str(session_id)[:8]}:{chain}"
        )
    except Exception:
        await _delayed()
