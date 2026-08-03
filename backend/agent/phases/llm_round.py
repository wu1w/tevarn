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
    from backend.kernel.llm_scheduler import (
        LlmAdmissionRejected,
        get_llm_admission,
        infer_request_from_loop,
    )

    result = LLMRoundResult(messages=messages)
    stream_usage: dict[str, int] = {}

    # ── LLM 公平调度准入（全局槽位 · 主人优先 · 日配额）────────
    _admission = get_llm_admission()
    _lease = None
    try:
        _lease = await _admission.acquire(infer_request_from_loop(loop))
    except LlmAdmissionRejected as e:
        logger.warning("LLM admission rejected: %s", e)
        result.action = "break"
        result.final_content = (
            f"[Scheduler] 未能获得模型槽位：{e.reason}。"
            "请稍后重试，或在内核「调度」面板查看排队与配额。"
        )
        return result
    except Exception as e:
        # 调度器故障不阻断主路径（可观测日志）
        logger.debug("LLM admission skip: %s", e)
        _lease = None

    try:
        return await _run_llm_round_body(
            loop,
            session_id=session_id,
            iteration=iteration,
            messages=messages,
            tools=tools,
            llm_service=llm_service,
            message_id=message_id,
            force_final_no_tools=force_final_no_tools,
            suppress_content_stream=suppress_content_stream,
            final_content=final_content,
            turn_retry=turn_retry,
            trace_thinking_steps=trace_thinking_steps,
            result=result,
            stream_usage=stream_usage,
            lease=_lease,
            admission=_admission,
        )
    finally:
        if _lease is not None:
            try:
                await _admission.release(_lease)
            except Exception as e:
                logger.debug("LLM lease release: %s", e)


async def _run_llm_round_body(
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
    result: LLMRoundResult,
    stream_usage: dict[str, int],
    lease: Any,
    admission: Any,
) -> LLMRoundResult:
    """准入获槽后的实际 LLM 轮逻辑。"""
    from backend.agent.robust import is_transient_llm_error
    from backend.agent.turn_retry import RetryKind

    accumulated_content = ""
    accumulated_reasoning = ""
    tool_calls: list[Any] = []

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
                if chunk.finish_reason == "error":
                    # P1：连接重置等 error 不得把半截正文当最终答复持久化
                    err_delta = (chunk.delta or "").strip()
                    body = (accumulated_content or "").strip()
                    if err_delta.startswith("[LLM Error") or not body:
                        accumulated_content = err_delta or (
                            "[LLM Error] 模型返回失败且无正文。"
                            "请检查网络/API Key/模型名后重试。"
                        )
                        result.action = "break"
                        result.final_content = accumulated_content
                        result.accumulated_content = accumulated_content
                        result.tool_calls = []
                        # 不落半截为成功 assistant
                        try:
                            loop.last_exit_reason = "llm_stream_error"
                        except Exception:
                            pass
                        return result
                    # 有正文 + error：去掉尾部错误文案，标记需用户知悉
                    if err_delta and body.endswith(err_delta):
                        accumulated_content = body[: -len(err_delta)].rstrip()
                    accumulated_content = (
                        (accumulated_content or "").rstrip()
                        + "\n\n[系统] 流式中断，以上为不完整草稿，请重试或点重新生成。"
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
            import uuid as _uuid

            delay = min(8.0, 0.8 * (2 ** (_retried - 1)))
            await loop._push_status(
                session_id,
                "thinking",
                f"LLM {_kind.value}，{_retried}/{_attempts} 次重试…",
            )
            await _aio.sleep(delay)
            # P1：重试用新 message_id，避免前端把全量重推拼到旧气泡（幽灵半截）
            try:
                message_id = _uuid.uuid4()  # noqa: F841 — 外层 loop 下一轮会新建；此处清缓冲
            except Exception:
                pass
            # 通知前端清 streaming 缓冲（status 即可）
            try:
                await loop._push_status(
                    session_id, "thinking", f"重试中 · 新一轮输出…"
                )
            except Exception:
                pass
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

        eng = get_context_engine(session_id)
        if stream_usage.get("prompt_tokens"):
            eng.update_from_response(dict(stream_usage))
        else:
            win = int(getattr(eng, "context_length", 0) or 0) or int(
                getattr(settings, "context_window", 128_000) or 128_000
            )
            est = TokenMeter(context_window=win).estimate_messages(messages)
            eng.update_from_response({
                "prompt_tokens": est,
                "completion_tokens": max(
                    8, round(len(accumulated_content or "") / 3.4)
                ),
            })
    except Exception:
        pass

    # ── Agent Kernel 预算强制（TC-B2）：进程级 token 预算扣减，超限中断 run ──
    # 优先 billable（cache miss + output）；多数流式 provider 不回填 usage → 粗估
    try:
        from backend.services.llm.usage_normalize import charge_amount_from_usage

        prefer_billable = bool(getattr(settings, "agent_budget_prefer_billable", True))
        spent = charge_amount_from_usage(
            stream_usage if stream_usage else None,
            prefer_billable=prefer_billable,
            fallback=0,
        )
    except Exception:
        spent = int(stream_usage.get("prompt_tokens") or 0) + int(
            stream_usage.get("completion_tokens") or 0
        )
    if spent <= 0:
        try:
            from backend.agent.token_meter import TokenMeter

            _pt = TokenMeter(
                context_window=int(
                    getattr(settings, "context_window", 128_000) or 128_000
                )
            ).estimate_messages(messages)
            _ct = max(8, round(len(accumulated_content or "") / 3.4))
            spent = int(_pt) + int(_ct)
        except Exception:
            spent = max(8, round(len(accumulated_content or "") / 3.4))

    # 日配额记账（全局/员工）
    if spent > 0 and admission is not None:
        try:
            _iid = getattr(lease, "identity_id", None) if lease else None
            if not _iid:
                _iid = getattr(loop, "_identity_id", None)
            admission.charge_quota(_iid, spent)
        except Exception as e:
            logger.debug("llm daily quota charge skip: %s", e)

    kernel_proc = getattr(loop, "_kernel_process", None)
    # P0.5 R1/R5：cost ledger（token/billable）；cache_record 在 log_cache_usage
    try:
        from backend.services.llm.usage_normalize import report_cost_to_kernel

        _bill = int(stream_usage.get("billable_tokens") or spent or 0)
        _tok = int(stream_usage.get("total_tokens") or spent or 0)
        # 归因优先级：真实 service.provider_id → _family() → settings catalog → model 启发
        # 必须用**本轮 llm_service** 上的 model，禁止回落成 settings 与 service 不一致时的错账
        _model = str(getattr(llm_service, "model", None) or "").strip()
        _fam = str(getattr(llm_service, "provider_id", None) or "").strip()
        if not _fam or _fam.lower() in (
            "custom",
            "generic",
            "openai-compatible",
            "openai_compatible",
            "default",
        ):
            if hasattr(llm_service, "_family") and callable(llm_service._family):
                try:
                    _fam = str(llm_service._family() or "").strip()
                except Exception:
                    _fam = ""
        if not _fam:
            _fam = str(
                getattr(settings, "llm_catalog_provider_id", None)
                or getattr(settings, "llm_provider", None)
                or "default"
            )
        if (not _fam or _fam in ("default", "generic", "openai-compatible")) and _model:
            try:
                from backend.services.llm.provider_profiles import _family_from_model

                _hint = _family_from_model(_model)
                if _hint:
                    _fam = _hint
            except Exception:
                pass
        if not _model:
            _model = str(getattr(settings, "llm_model", None) or "").strip()
        logger.info(
            "usage charge family=%s model=%s tokens=%s billable=%s",
            _fam,
            _model or "?",
            _tok,
            _bill if _bill > 0 else _tok,
        )
        report_cost_to_kernel(
            process_id=getattr(kernel_proc, "id", None) if kernel_proc else None,
            family=_fam,
            tokens=_tok,
            billable=_bill if _bill > 0 else _tok,
            model=_model or None,
        )

    except Exception as _cost_e:
        logger.warning("cost_charge skip: %s", _cost_e)

    if kernel_proc is not None and kernel_proc.token_budget is not None:
        from backend.kernel import BudgetExceededError, get_kernel

        try:
            if spent > 0:
                get_kernel().charge_tokens(kernel_proc.id, spent)
        except BudgetExceededError as e:
            # Dynamic budget: interactive CEO + limited workforce auto top_up.
            # Workforce used to hard-stop → budget-fail churn; now allows a few
            # renewals so long jobs can finish without CEO babysitting.
            _recovered = False
            try:
                _origin = str(getattr(loop, "_run_origin", "") or "").lower()
                _meta = getattr(kernel_proc, "meta", None) or {}
                _is_wf = bool(_meta.get("workforce")) or str(
                    getattr(kernel_proc, "identity", "") or ""
                ).startswith("wf:")
                _interactive = (not _is_wf) and _origin in (
                    "",
                    "chat",
                    "default",
                    "goal",
                )
                # Workforce opt-out: payload/meta hard_cap_only or global flag
                _wf_auto = True
                try:
                    if _is_wf and bool(
                        getattr(settings, "agent_workforce_hard_cap_only", False)
                    ):
                        _wf_auto = False
                    if isinstance(_meta, dict) and _meta.get("hard_cap_only") in (
                        True,
                        "true",
                        1,
                        "1",
                    ):
                        _wf_auto = False
                except Exception:
                    pass
                # hard_cap_only 只挡经典 soft_renew；编制有限次 auto 与 chat_elastic 独立
                _wf_auto_enabled = bool(
                    getattr(settings, "agent_workforce_auto_top_up_enabled", True)
                )
                if _interactive or (_is_wf and _wf_auto and _wf_auto_enabled):
                    _k = get_kernel()
                    _n = int(
                        (_meta.get("auto_top_up_count") or 0)
                        if isinstance(_meta, dict)
                        else 0
                    )
                    if _interactive:
                        _max = int(
                            getattr(settings, "agent_chat_auto_top_up_max", 16) or 16
                        )
                        _min_add = int(
                            getattr(settings, "agent_chat_auto_top_up_min_add", 250_000)
                            or 250_000
                        )
                        _add = max(_min_add, int(spent) * 3, 300_000)
                        _add = min(_add, 1_000_000)
                        _by = "system:interactive_auto"
                    else:
                        # Workforce: 比主会话更紧（默认 max=3 / min 100k / 单次 ≤400k）
                        _max = int(
                            getattr(settings, "agent_workforce_auto_top_up_max", 3) or 3
                        )
                        _min_add = int(
                            getattr(
                                settings, "agent_workforce_auto_top_up_min_add", 100_000
                            )
                            or 100_000
                        )
                        _add = max(_min_add, int(spent) * 2, 150_000)
                        _add = min(_add, 400_000)
                        try:
                            _cap = int(
                                getattr(
                                    settings,
                                    "agent_workforce_budget_hard_cap",
                                    2_000_000,
                                )
                                or 2_000_000
                            )
                            _bud = int(getattr(kernel_proc, "token_budget", 0) or 0)
                            if _cap > 0 and _bud + _add > _cap:
                                _add = max(0, _cap - _bud)
                        except Exception:
                            pass
                        _by = "system:workforce_auto"
                    if _n < _max and _add > 0:
                        _k.top_up_budget(
                            kernel_proc.id,
                            _add,
                            by=_by,
                            reason=f"auto top_up after BudgetExceeded (n={_n + 1})",
                        )
                        if isinstance(_meta, dict):
                            _meta = dict(_meta)
                            _meta["auto_top_up_count"] = _n + 1
                            try:
                                kernel_proc.meta = _meta  # type: ignore[misc]
                            except Exception:
                                pass
                        _k.charge_tokens(kernel_proc.id, spent)
                        fresh = _k.get_process(kernel_proc.id)
                        if fresh is not None:
                            loop._kernel_process = fresh
                        _recovered = True
                        logger.info(
                            "%s auto top_up ok proc=%s add=%s n=%s",
                            "interactive" if _interactive else "workforce",
                            kernel_proc.id,
                            _add,
                            _n + 1,
                        )
                        try:
                            await loop._push_status(
                                session_id,
                                "thinking",
                                f"Token 预算动态追加 +{_add}（第 {_n + 1}/{_max} 次），继续…",
                            )
                        except Exception:
                            pass
            except Exception as _tu_e:
                logger.warning("auto top_up failed: %s", _tu_e)
                _recovered = False

            if not _recovered:
                logger.warning(
                    "kernel token 预算耗尽，中断 run proc=%s: %s", kernel_proc.id, e
                )
                loop._should_stop = True
                result.action = "break"
                try:
                    from backend.agent.exit_reasons import format_exit_user_message

                    # Token budget — NOT iteration budget (was mislabeled as 迭代预算耗尽)
                    loop.last_exit_reason = "kernel_token_budget_exhausted"
                    result.final_content = (
                        format_exit_user_message(
                            "kernel_token_budget_exhausted",
                            process_id=kernel_proc.id,
                        )
                        + f"\n（{e}）\n禁止用报告框架/预期结果冒充结论。"
                    )
                except Exception:
                    result.final_content = (
                        f"[Token 预算耗尽] 进程 token 额度用尽，运行已中断（{e}）。"
                        "请 top_up 或提高默认 token_budget 后重试。"
                        "禁止用报告框架/预期结果冒充结论。"
                    )
                result.accumulated_content = ""
                result.tool_calls = []
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
