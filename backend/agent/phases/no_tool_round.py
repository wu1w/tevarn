"""无工具分支 phase（loop 拆分第四刀）

从 loop.py _run_locked 抽出的「LLM 本轮无 tool calls」分支
（原 1104-1238 行）：goal nudge / 空回复重试 / 完成度校验 / 收尾定稿。
行为冻结：tests/test_loop_freeze.py（拆分前后同绿）。

外层 continue/break 语义映射为 NoToolRoundResult.action：
- "continue"：nudge/重试/followup 后进入下一迭代
- "break"：定稿，final_content 已填
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class NoToolRoundResult:
    action: str = "break"  # continue | break
    final_content: str | None = None
    force_final_no_tools: bool | None = None  # None 表示不变
    goal_nudge_count: int = 0
    completion_followups: int = 0


async def run_no_tool_round(
    loop: Any,
    *,
    session_id: uuid.UUID,
    iteration: int,
    seg_size: int,
    messages: list[dict[str, Any]],
    accumulated_content: str,
    accumulated_reasoning: str = "",
    goal_mode: bool,
    goal_nudge_count: int,
    turn_retry: Any,
    empty_reply_max: int,
    last_tool_round_count: int,
    force_final_no_tools: bool,
    user_input: str,
    enriched_input: str,
    tools_used_run: list[str],
    completion_followups: int,
) -> NoToolRoundResult:
    """无 tool calls 分支：goal nudge → 空回复重试 → 完成度校验 → 定稿"""
    from backend.agent.robust import is_empty_assistant_content
    from backend.agent.turn_retry import RetryKind

    result = NoToolRoundResult(
        goal_nudge_count=goal_nudge_count,
        completion_followups=completion_followups,
    )

    # P2: Plan 模式无工具 → 收计划
    if getattr(loop, "_plan_mode_active", False) and (accumulated_content or "").strip():
        try:
            from backend.agent.plan_session import submit_plan_markdown, get_gate
            from backend.agent.plan_gate import PlanState
            _body = (accumulated_content or "").strip()
            if len(_body) > 40:
                submit_plan_markdown(_body, session_id=str(session_id))
                _g = get_gate(session_id=str(session_id))
                _tail = (
                    "\n\n---\n计划已就绪，状态：**plan_ready**。"
                    "回复「批准计划」或「按计划执行」后开始改代码；"
                    "「推翻计划」可重来。"
                    "（裸「开始执行」不会批准，避免误触。）"
                )
                if _g.state == PlanState.PLAN_READY and "批准计划" not in _body:
                    result.final_content = _body + _tail
                else:
                    result.final_content = accumulated_content
                result.action = "break"
                try:
                    loop.last_exit_reason = "plan_ready"
                except Exception:
                    pass
                logger.info("plan submitted session=%s len=%s", session_id, len(_body))
                return result
        except Exception as _ps_e:
            logger.debug("plan submit skip: %s", _ps_e)


    # ── Goal 已完成：最多 1 次「完整总结」nudge，禁止短回复死循环复读 ──
    # 旧逻辑：content < 120 就 continue，且不看 force_final → 每轮短答再 nudge，
    # 手机端看到同段话反复刷（日志: goal complete summary nudge × N）。
    if not loop._should_stop:
        try:
            from backend.agent.goal_state import get_goal as _gg_done
            from backend.agent.robust import is_empty_assistant_content as _empty_done

            _g_done = _gg_done(session_id)
            _short = _empty_done(accumulated_content) or len(
                (accumulated_content or "").strip()
            ) < 120
            _already_nudged = bool(
                force_final_no_tools
                or getattr(loop, "_goal_complete_summary_nudged", False)
            )
            if (
                _g_done is not None
                and _g_done.is_complete()
                and _short
                and not _already_nudged
            ):
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "[System] Goal is complete. Tools optional. "
                            "Write a full user-facing summary in the user's language: "
                            "what was done, verification evidence (e.g. cargo check), "
                            "remaining risks, and suggested follow-ups. "
                            "Do not reply with only a one-liner or empty body."
                        ),
                    }
                )
                result.force_final_no_tools = True
                result.action = "continue"
                try:
                    loop._goal_complete_summary_nudged = True
                except Exception:
                    pass
                logger.info(
                    "goal complete summary nudge session=%s (once)",
                    session_id,
                )
                return result
            # 已 nudge 过仍短：直接定稿，避免 40 轮复读烧 token
            if (
                _g_done is not None
                and _g_done.is_complete()
                and _short
                and _already_nudged
            ):
                logger.info(
                    "goal complete summary already nudged — break session=%s len=%s",
                    session_id,
                    len((accumulated_content or "").strip()),
                )
                result.action = "break"
                result.final_content = (
                    accumulated_content
                    or accumulated_reasoning
                    or "[Goal complete]"
                )
                try:
                    loop.last_exit_reason = "goal_complete_short_summary"
                except Exception:
                    pass
                return result
        except Exception as _gcd_e:
            logger.debug("goal complete summary nudge skip: %s", _gcd_e)

    
    # S8: 非 Goal 有正文即定稿
    if not goal_mode and not force_final_no_tools and not loop._should_stop:
        try:
            from backend.agent.goal_state import get_goal as _gg_ng
            _g_ng = _gg_ng(session_id)
            _has_active_goal = (
                _g_ng is not None
                and not _g_ng.is_complete()
                and str(getattr(_g_ng, "status", "") or "") not in ("cancelled", "completed")
            )
        except Exception:
            _has_active_goal = False
        if not _has_active_goal:
            _body = (accumulated_content or "").strip()
            if _body and not is_empty_assistant_content(accumulated_content):
                result.action = "break"
                result.final_content = accumulated_content
                try:
                    loop.last_exit_reason = "non_goal_text_final"
                except Exception:
                    pass
                logger.info("no_tool non-goal finalize len=%s session=%s", len(_body), session_id)
                return result

    # ── Goal 未完成：禁止 text-only 假收工（不限 mode=goal）──
    # 自动续跑常以 mode=default 注入 Goal 摘要，旧逻辑只在 goal_mode 下 nudge，
    # 导致模型说「继续读…」却不调工具 → run 直接 done。
    if not force_final_no_tools and not loop._should_stop:
        try:
            from backend.agent.goal_state import get_goal
            from backend.core.config import settings as _st

            g = get_goal(session_id)
            incomplete = (
                g is not None
                and not g.is_complete()
                and str(getattr(g, "status", "") or "") not in ("cancelled", "completed")
            )
            # 无 todo 的 active goal / goal_mode 空规划
            no_plan = bool(
                goal_mode
                and (g is None or (not getattr(g, "todos", None) and g.status != "completed"))
            ) or (
                g is not None
                and str(getattr(g, "status", "") or "") == "active"
                and not getattr(g, "todos", None)
            )
            keep = bool(getattr(_st, "agent_goal_incomplete_keep_going", True))
            max_nudges = max(
                8, int(getattr(_st, "agent_goal_incomplete_nudge_max", 16) or 16)
            )
            # 有未完成 Goal 时始终 keep-going；纯 goal_mode 空规划同样
            should_nudge = keep and (incomplete or no_plan) and goal_nudge_count < max_nudges
            if should_nudge:
                result.goal_nudge_count += 1
                if accumulated_content:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": accumulated_content,
                        }
                    )
                    await loop._push_status(
                        session_id,
                        "thinking",
                        f"Goal 未完成，自动续跑 ({result.goal_nudge_count}/{max_nudges})…",
                    )
                nudge = (
                    "[System] Goal is incomplete — do not finish with text only. "
                    "Call tools now:\n"
                    "1) manage_goal(action=get) for remaining todos;\n"
                    "2) file_write/edit code, or cargo check;\n"
                    "3) update_todo / manage_goal(complete).\n"
                    "Do not only say 'reading/aligning' without tools. "
                    "Reply to the user in their language."
                )
                if g:
                    nudge += "\n\n" + g.summary_for_llm()
                messages.append({"role": "user", "content": nudge})
                # 确保下一轮可调工具
                result.force_final_no_tools = False
                logger.info(
                    "Goal incomplete auto-continue nudge=%s/%s session=%s goal_mode=%s",
                    result.goal_nudge_count,
                    max_nudges,
                    session_id,
                    goal_mode,
                )
                result.action = "continue"
                return result
        except Exception as _gn_e:
            logger.debug("goal incomplete nudge skip: %s", _gn_e)

    # ── 空正文：TurnRetryState 分类重试 / 耗尽则 force_final ──
    # 注意：force_final 后再空 → 必须 break，否则会 100+ 轮空转（has_tools=False）
    if is_empty_assistant_content(accumulated_content) and not loop._should_stop:
        # Already in force_final and still blank → stop the run (no more thrash)
        if force_final_no_tools:
            logger.warning(
                "empty content after force_final — break thrash session=%s",
                session_id,
            )
            result.action = "break"
            result.final_content = (
                accumulated_content
                or accumulated_reasoning
                or "[Stopped] Model returned empty replies repeatedly. Please retry."
            )
            try:
                loop.last_exit_reason = "empty_content_thrash"
            except Exception:
                pass
            return result
        action = turn_retry.note_and_decide(
            RetryKind.EMPTY_CONTENT, detail="empty assistant content"
        )
        _empty_reply_retries = int(
            turn_retry.counts.get(RetryKind.EMPTY_CONTENT.value, 0)
        )
        if action == "retry" and _empty_reply_retries <= empty_reply_max:
            await loop._push_status(
                session_id,
                "thinking",
                f"模型空回复，重试 {_empty_reply_retries}/{empty_reply_max}…",
            )
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Your last turn had no visible body. Answer the user in "
                        "their language with natural prose."
                        + (
                            " You already used tools: give a final answer from "
                            "tool results (e.g. key stdout lines); do not return blank."
                            if last_tool_round_count > 0
                            else " If you need file/command facts, call tools first; "
                            "do not return blank only."
                        )
                    ),
                }
            )
            logger.info(
                "Empty assistant reply retry %s session=%s action=%s",
                _empty_reply_retries,
                session_id,
                action,
            )
            result.action = "continue"
            return result
        if action == "force_final":
            # Only one force_final attempt for empty content
            if _empty_reply_retries > empty_reply_max + 1:
                logger.warning(
                    "empty content force_final exhausted — break session=%s n=%s",
                    session_id,
                    _empty_reply_retries,
                )
                result.action = "break"
                result.final_content = (
                    accumulated_reasoning
                    or "[Stopped] Empty model replies. Please retry the last message."
                )
                try:
                    loop.last_exit_reason = "empty_content_thrash"
                except Exception:
                    pass
                return result
            result.force_final_no_tools = True
            await loop._push_status(
                session_id,
                "thinking",
                "空回复重试耗尽，强制生成最终文字…",
            )
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Immediately give a concise final answer in the user's "
                        "language. Do not call more tools."
                    ),
                }
            )
            result.action = "continue"
            return result

    # ── needsFollowUp：声称完成但未做必要写入/验证 → 强制再来一轮 ──
    if not force_final_no_tools and not loop._should_stop:
        try:
            from backend.agent.completion_gate import evaluate_completion

            # Durable Run：进入完成度校验阶段
            _rc = getattr(loop, "_run_recorder", None)
            if _rc is not None:
                try:
                    from backend.agent.run_recorder import RunStatus as _RS

                    await _rc.transition(_RS.VERIFYING)
                except Exception:
                    pass

            _model = (
                getattr(loop, "_model_name", None)
                or getattr(loop, "model", None)
                or getattr(loop, "_model", None)
            )
            if _model is not None and not isinstance(_model, str):
                _model = getattr(_model, "name", None) or str(_model)
            _ver = evaluate_completion(
                user_input or enriched_input or "",
                tools_used_run,
                accumulated_content or "",
                max_followups_done=completion_followups,
                model_name=str(_model) if _model else None,
            )
            if not _ver.ok and _ver.nudge:
                result.completion_followups += 1
                await loop._push_status(
                    session_id,
                    "thinking",
                    f"补充取证（{_ver.reason}）…",
                )
                messages.append(
                    {"role": "system", "content": _ver.nudge}
                )
                # allow tools again
                result.force_final_no_tools = False
                logger.info(
                    "completion gate followup=%s reason=%s session=%s",
                    result.completion_followups,
                    _ver.reason,
                    session_id,
                )
                result.action = "continue"
                return result
        except Exception as _cg_e:
            logger.debug("completion gate skipped: %s", _cg_e)

    # ── 得到最终回复 ──
    result.action = "break"
    result.final_content = accumulated_content
    return result
