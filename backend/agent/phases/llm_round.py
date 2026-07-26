"""LLM 调用轮 phase（loop 拆分第三刀）

从 loop.py _run_locked 抽出的「流式 LLM 调用 + 失败重试 + 应急压缩 + 空工具名处理」
（原 985-1179 行）。行为冻结：tests/test_loop_freeze.py（拆分前后同绿）。

外层 while 循环的 continue/break 语义映射为 LLMRoundResult.action：
- "proceed"：正常完成本轮（可能带 tool_calls）
- "continue"：重试/应急压缩后直接进入下一迭代
- "break"：停止/错误收尾，final_content 已填
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from backend.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class LLMRoundResult:
    accumulated_content: str = ""
    accumulated_reasoning: str = ""
    tool_calls: list[Any] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    action: str = "proceed"  # proceed | continue | break
    final_content: str | None = None
    # 空工具名 force_final 时置 True；None 表示不变
    force_final_no_tools: bool | None = None


def _msg_chars(m: dict[str, Any]) -> int:
    """调试日志：content 可能是 None（assistant+tool_calls），不能 len(None)"""
    c = m.get("content")
    if c is None:
        return 0
    if isinstance(c, str):
        return len(c)
    if isinstance(c, list):
        try:
            return len(json.dumps(c, ensure_ascii=False))
        except Exception:
            return 0
    return len(str(c))


async def run_llm_round(
    loop: Any,
    *,
    session_id: uuid.UUID,
    iteration: int,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    llm_service: Any,
    message_id: uuid.UUID,
    force_final_no_tools: bool,
    suppress_content_stream: bool,
    final_content: str | None,
    turn_retry: Any,
    trace_thinking_steps: list[dict[str, Any]],
) -> LLMRoundResult:
    """执行一轮流式 LLM 调用（含重试/应急压缩/空工具名处理）"""
    from backend.agent.robust import is_transient_llm_error
    from backend.agent.turn_retry import RetryKind

    result = LLMRoundResult(messages=messages)
    accumulated_content = ""
    accumulated_reasoning = ""
    tool_calls: list[Any] = []
    stream_usage: dict[str, int] = {}

    try:
        logger.info(
            f"Sending {len(messages)} messages to LLM "
            f"(total chars: {sum(_msg_chars(m) for m in messages)})"
        )
        _iter_tools = None if force_final_no_tools else (tools if tools else None)
        async for chunk in llm_service.chat(
            messages, tools=_iter_tools, stream=True
        ):
            # 思考中可打断
            if loop._should_stop:
                logger.info(
                    "Stop during LLM stream for session %s", session_id
                )
                break

            # 推送流式文本到前端
            if chunk.delta:
                accumulated_content += chunk.delta
                if not suppress_content_stream:
                    await loop._push_stream(
                        session_id, message_id, chunk.delta
                    )

            # 思考链增量（不进前端 stream，仅汇总给通道 progress）
            rdelta = getattr(chunk, "reasoning_delta", None) or ""
            if rdelta:
                accumulated_reasoning += rdelta

            # 收集 tool call
            if chunk.tool_call:
                tool_calls.append(chunk.tool_call)

            # 真实用量（T4）：provider 回填时优先于粗估
            _cu = getattr(chunk, "usage", None)
            if isinstance(_cu, dict) and _cu:
                stream_usage.update(_cu)

            # 结束标记
            if chunk.finish_reason:
                if chunk.finish_reason == "error" and not (accumulated_content or "").strip():
                    if chunk.delta:
                        accumulated_content = chunk.delta
                    else:
                        accumulated_content = (
                            "[LLM Error] 模型返回失败且无正文。"
                            "若使用 Kimi Plan/Kimi Code，请将模型设为 "
                            "kimi-for-coding 或 kimi-for-coding-highspeed（不要用 k3）。"
                        )
                break

        if loop._should_stop:
            result.action = "break"
            result.final_content = (
                accumulated_content
                or final_content
                or "[Stopped] Generation was cancelled"
            )
            result.accumulated_content = accumulated_content
            result.accumulated_reasoning = accumulated_reasoning
            result.tool_calls = tool_calls
            return result

    except Exception as e:
        logger.error(f"LLM chat error in iteration {iteration + 1}: {e}")
        # 413 / context overflow → reactiveCompact then retry once
        try:
            from backend.agent.context_compress import (
                is_prompt_too_long_error,
                reactive_compact_if_needed,
            )
            if is_prompt_too_long_error(e) and not getattr(loop, "_reactive_compact_used", False):
                loop._reactive_compact_used = True
                result.messages, _rmeta = await reactive_compact_if_needed(
                    messages, session_id=session_id, force=True
                )
                await loop._push_status(
                    session_id, "optimizing", "上下文过长，已应急压缩并重试…"
                )
                result.action = "continue"
                return result
        except Exception as _rc_e:
            logger.warning("reactiveCompact failed: %s", _rc_e)

        from backend.agent.turn_retry import classify_llm_error

        _kind = classify_llm_error(e)
        _action = turn_retry.note_and_decide(_kind, detail=str(e)[:160])
        _attempts = int(getattr(settings, "agent_llm_retry_attempts", 3) or 1)
        _retried = getattr(loop, "_llm_fail_streak", 0) + 1
        loop._llm_fail_streak = _retried
        _can = (
            _retried < _attempts
            and _action == "retry"
            and (
                is_transient_llm_error(e)
                or _kind
                in (
                    RetryKind.RATE_LIMIT,
                    RetryKind.TOOL_TRANSIENT,
                    RetryKind.TOOL_TIMEOUT,
                )
            )
            and not loop._should_stop
        )
        if _can:
            import asyncio as _aio

            delay = min(8.0, 0.8 * (2 ** (_retried - 1)))
            await loop._push_status(
                session_id,
                "thinking",
                f"LLM {_kind.value}，{_retried}/{_attempts} 次重试…",
            )
            await _aio.sleep(delay)
            result.action = "continue"
            return result
        loop._llm_fail_streak = 0
        await loop._push_status(session_id, "error", f"LLM 调用失败: {e}")
        result.action = "break"
        result.final_content = f"[Error] LLM service failed: {e}"
        return result

    # 引擎层回写：有 provider 真实 usage 就用真值，否则粗估（驱动后续是否再压缩）
    try:
        from backend.agent.context_engine import get_context_engine
        from backend.agent.token_meter import TokenMeter

        eng = get_context_engine()
        if stream_usage.get("prompt_tokens"):
            eng.update_from_response(dict(stream_usage))
        else:
            est = TokenMeter(
                context_window=int(
                    getattr(settings, "context_window", 128_000) or 128_000
                )
            ).estimate_messages(messages)
            eng.update_from_response({
                "prompt_tokens": est,
                "completion_tokens": max(
                    8, round(len(accumulated_content or "") / 3.4)
                ),
            })
    except Exception:
        pass

    # ── Agent Kernel 预算强制（TC-B2）：进程级 token 预算扣减，超限中断 run ──
    kernel_proc = getattr(loop, "_kernel_process", None)
    if kernel_proc is not None and kernel_proc.token_budget is not None:
        from backend.kernel import BudgetExceededError, get_kernel

        try:
            spent = int(stream_usage.get("prompt_tokens") or 0) + int(
                stream_usage.get("completion_tokens") or 0
            )
            if spent > 0:
                get_kernel().charge_tokens(kernel_proc.id, spent)
        except BudgetExceededError as e:
            logger.warning("kernel 预算耗尽，中断 run proc=%s: %s", kernel_proc.id, e)
            loop._should_stop = True
            result.action = "break"
            result.final_content = (
                f"[Budget Exceeded] 进程 token 预算耗尽，运行已中断（{e}）。"
                "可在创建进程时提高预算或拆小任务。"
            )
            return result
        except Exception as e:
            logger.debug("kernel charge_tokens 跳过: %s", e)

    # 本轮 LLM 成功，重置失败计数
    loop._llm_fail_streak = 0

    # 通道进度：优先 reasoning，其次可见 content（不含 tool 调用细节）
    _think = (accumulated_reasoning or accumulated_content or "").strip()
    if _think:
        await loop._emit_progress("thinking", _think[:1200])
        trace_thinking_steps.append({
            "iteration": iteration + 1,
            "content": (accumulated_reasoning or "")[:800],
            "visible_content": (accumulated_content or "")[:400],
            "has_tool_calls": bool(tool_calls),
        })

    # 判断是否有 tool calls
    if tool_calls:
        _raw_tcs = list(tool_calls)
        tool_calls = [
            tc
            for tc in _raw_tcs
            if (getattr(tc, "name", None) or "").strip()
        ]
        if not tool_calls and _raw_tcs:
            _act = turn_retry.note_and_decide(
                RetryKind.EMPTY_TOOL_NAME, detail="empty tool name"
            )
            await loop._push_status(
                session_id,
                "thinking",
                "模型返回空工具名，已拒绝并重试…",
            )
            result.messages.append(
                {
                    "role": "system",
                    "content": (
                        "上一轮 tool call 的 name 为空，已被拒绝。"
                        "请使用已提供的合法工具名重新调用，或直接文字作答。"
                    ),
                }
            )
            if _act == "force_final":
                result.force_final_no_tools = True
            if _act in ("retry", "force_final"):
                result.action = "continue"
                result.accumulated_content = accumulated_content
                result.accumulated_reasoning = accumulated_reasoning
                return result
            result.action = "break"
            result.final_content = (
                accumulated_content
                or "[Error] 模型返回了无效的空工具调用"
            )
            result.accumulated_content = accumulated_content
            result.accumulated_reasoning = accumulated_reasoning
            return result

    result.accumulated_content = accumulated_content
    result.accumulated_reasoning = accumulated_reasoning
    result.tool_calls = tool_calls
    return result
