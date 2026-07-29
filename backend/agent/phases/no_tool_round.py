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

    # ── Goal 模式 nudge ──
    if goal_mode:
        from backend.agent.goal_state import get_goal

        g = get_goal(session_id)
        incomplete = g is not None and not g.is_complete()
        # 无 todo 也算未规划完成（允许 1 次纯文本规划后必须建 todo）
        no_plan = g is None or (not g.todos and g.status != "completed")
        if (incomplete or no_plan) and goal_nudge_count < 8 and iteration < seg_size - 1:
            result.goal_nudge_count += 1
            # 把当前文本当作中间思考，要求继续
            if accumulated_content:
                messages.append(
                    {
                        "role": "assistant",
                        "content": accumulated_content,
                    }
                )
                # 中间内容推到流，便于 UI 展示
                await loop._push_status(
                    session_id,
                    "thinking",
                    f"Goal 未完成，继续执行 ({result.goal_nudge_count})…",
                )
            nudge = (
                "Goal 尚未达成。请：\n"
                "1) 若还没有 todo，调用 manage_goal 创建任务列表；\n"
                "2) 推进未完成项并 update_todo；\n"
                "3) 全部完成后 manage_goal(action=complete) 再给出最终总结。\n"
                "不要在未完成时停止。"
            )
            if g:
                nudge += "\n\n" + g.summary_for_llm()
            messages.append({"role": "user", "content": nudge})
            logger.info(
                f"Goal nudge #{result.goal_nudge_count} for session {session_id}"
            )
            result.action = "continue"
            return result

    # ── 空正文：TurnRetryState 分类重试 / 耗尽则 force_final ──
    if is_empty_assistant_content(accumulated_content) and not loop._should_stop:
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
                        "你上一轮没有输出任何可见正文。请直接用自然语言回答用户。"
                        + (
                            "你刚才已调用过工具：必须根据工具结果给出最终中文答复"
                            "（例如复述 command 的 stdout 关键行），禁止只返回空白。"
                            if last_tool_round_count > 0
                            else "若需要文件/命令事实，先调用工具再回答；不要只输出空白。"
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
                        "请立刻用简洁中文给出最终回答，不要再调用工具。"
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

            _model = None
            try:
                _model = getattr(loop, "_llm_model_name", None) or getattr(
                    loop, "model_name", None
                )
            except Exception:
                _model = None
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
