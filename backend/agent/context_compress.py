"""
会话上下文压缩：兼容入口。

实现已迁移到 ContextPipeline (L1/L3/L5)，语义对齐 Claude Code：
  - L5 注入「续跑」指令（非 reference-only 禁令）
  - mid-loop 传 allow_l5=False，只做 micro
本模块保留 `compress_history_if_needed` 与 `estimate_msgs_tokens` 给旧调用方。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from backend.agent.context_engine import (
    COMPRESS_THRESHOLD,
    COMPRESS_THRESHOLD_DEEP,
    get_context_engine,
)
from backend.agent.token_meter import TokenMeter
from backend.core.config import settings

logger = logging.getLogger(__name__)


def estimate_msgs_tokens(messages: list[dict[str, Any]]) -> int:
    meter = TokenMeter(
        context_window=int(getattr(settings, "context_window", 128_000) or 128_000)
    )
    return meter.estimate_messages(messages)


async def compress_history_if_needed(
    messages: list[dict[str, Any]],
    *,
    session_id: uuid.UUID | None = None,
    threshold: float = COMPRESS_THRESHOLD,  # audit-fix(#1)：0.75 → 单点常量 0.85
    allow_l5: bool = True,
    micro_only: bool = False,
    light_loop: bool = False,
    ops_loop: bool = False,
    history_cap: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    若估算 token 超过阈值预算，运行 pipeline (L1/L3/L5)。

    allow_l5 / micro_only:
      工具轮 mid-loop 应传 allow_l5=False（或 micro_only=True），
      只做 L1/L3，避免 Claude Code 风格的「全量摘要」打断同轮长任务。

    注意：阈值判断用**本调用局部 TokenMeter**，不再改写全局 engine.threshold_percent
   （多 session 并发压缩时避免互相踩阈值）。
    """
    engine = get_context_engine(session_id)
    thr = float(
        threshold
        or getattr(engine, "threshold_percent", COMPRESS_THRESHOLD)
        or COMPRESS_THRESHOLD
    )
    window = int(
        getattr(engine, "context_length", 0)
        or getattr(settings, "context_window", 128_000)
        or 128_000
    )
    local_meter = TokenMeter(context_window=window, threshold_percent=thr)

    tokens = estimate_msgs_tokens(messages)
    n_msg = len(messages)
    soft_n = int(getattr(settings, "context_max_messages_soft", 48) or 48)
    hard_n = int(getattr(settings, "context_max_messages_hard", 90) or 90)
    # audit-fix(#11)：条数压力排除非首条 system nudge
    n_effective = sum(
        1
        for i, m in enumerate(messages)
        if not (isinstance(m, dict) and m.get("role") == "system" and i > 0)
    )
    meta_base: dict[str, Any] = {
        "compressed": False,
        "tokens_before": tokens,
        "context_window": window,
        "budget": getattr(local_meter, "threshold_tokens", 0) or 0,
        "threshold_percent": thr,
        "message_count": n_msg,
        "effective_count": n_effective,
    }

    # Rust compact decision (authority); assembly stays Python
    decision: dict[str, Any] = {}
    try:
        import os

        be = (os.environ.get("TAKTON_KERNEL_BACKEND") or "").strip().lower()
        if be not in {"python", "py", "off", "0", "none"}:
            from backend.kernel import get_kernel

            k = get_kernel()
            params = {
                "tokens": tokens,
                "message_count": n_msg,
                "effective_count": n_effective,
                "context_window": window,
                "threshold_percent": thr,
                "soft_n": soft_n,
                "hard_n": hard_n,
                "micro_only": bool(micro_only),
                "allow_l5": bool(allow_l5),
                "light_loop": bool(light_loop),
                "ops_loop": bool(ops_loop),
                "history_cap": int(
                    history_cap
                    or getattr(settings, "agent_chat_history_hard_cap", 0)
                    or 0
                ),
            }
            if hasattr(k, "context_decide_compact"):
                decision = k.context_decide_compact(**params) or {}
            elif hasattr(k, "_call"):
                decision = k._call("context_decide_compact", params) or {}
            if isinstance(decision, dict) and decision.get("authority") == "rust":
                thr = float(decision.get("threshold_percent") or thr)
                allow_l5 = bool(decision.get("allow_l5", allow_l5))
                soft_n = int(decision.get("soft_n") or soft_n)
                hard_n = int(decision.get("hard_n") or hard_n)
                local_meter = TokenMeter(context_window=window, threshold_percent=thr)
                meta_base["threshold_percent"] = thr
                meta_base["compact_decision"] = {
                    k2: decision.get(k2)
                    for k2 in (
                        "should_compress",
                        "reasons",
                        "force_hard_truncate",
                        "skip_pipeline",
                        "authority",
                    )
                }
                if decision.get("skip_pipeline"):
                    if hasattr(engine, "_l1_budget"):
                        out, n = engine._l1_budget([dict(m) for m in messages])  # type: ignore[attr-defined]
                        if n:
                            meta_base.update(
                                {
                                    "compressed": True,
                                    "tokens_after": estimate_msgs_tokens(out),
                                    "layers": [f"L1:{n}"],
                                }
                            )
                            return out, meta_base
                    return messages, meta_base
                count_pressure = bool(decision.get("should_compress")) and (
                    "count_soft" in (decision.get("reasons") or [])
                    or "count_hard" in (decision.get("reasons") or [])
                )
                if count_pressure:
                    meta_base["count_pressure"] = True
                over = bool(decision.get("should_compress")) or engine.should_compress_preflight(
                    messages
                )
                force_hard = bool(decision.get("force_hard_truncate"))
            else:
                decision = {}
    except Exception as e:
        logger.debug("context_decide_compact skip: %s", e)
        decision = {}

    if not decision:
        # Python fallback (previous logic)
        count_pressure = n_effective >= soft_n
        if count_pressure and not micro_only:
            allow_l5 = True
            thr = min(thr, COMPRESS_THRESHOLD_DEEP)
            local_meter = TokenMeter(context_window=window, threshold_percent=thr)
            meta_base["count_pressure"] = True
            meta_base["threshold_percent"] = thr
        over = (
            local_meter.should_compress(tokens)
            or engine.should_compress_preflight(messages)
            or count_pressure
            or n_msg >= hard_n
        )
        force_hard = n_msg >= hard_n
        if not over and n_msg < 8:
            if hasattr(engine, "_l1_budget"):
                out, n = engine._l1_budget([dict(m) for m in messages])  # type: ignore[attr-defined]
                if n:
                    meta_base.update(
                        {
                            "compressed": True,
                            "tokens_after": estimate_msgs_tokens(out),
                            "layers": [f"L1:{n}"],
                        }
                    )
                    return out, meta_base
            return messages, meta_base
    else:
        if not over:
            if hasattr(engine, "_l1_budget"):
                out, n = engine._l1_budget([dict(m) for m in messages])  # type: ignore[attr-defined]
                if n:
                    meta_base.update(
                        {
                            "compressed": True,
                            "tokens_after": estimate_msgs_tokens(out),
                            "layers": [f"L1:{n}"],
                        }
                    )
                    return out, meta_base
            return messages, meta_base

    # 超硬上限：先硬砍中间，再走 pipeline
    work = messages
    if force_hard and hasattr(engine, "_hard_truncate"):
        try:
            old_tail = getattr(engine, "protect_last_n", 8)
            try:
                engine.protect_last_n = min(int(old_tail), 6)  # type: ignore[attr-defined]
                work, n_drop = engine._hard_truncate([dict(m) for m in work])  # type: ignore[attr-defined]
            finally:
                engine.protect_last_n = old_tail  # type: ignore[attr-defined]
            if n_drop:
                meta_base["pre_hard_drop"] = n_drop
                tokens = estimate_msgs_tokens(work)
        except Exception:
            work = messages

    out, meta = await engine.compress(
        work,
        current_tokens=tokens,
        session_id=session_id,
        allow_l5=allow_l5,
        micro_only=micro_only,
    )
    meta_base.update(meta)
    return out, meta_base


def is_prompt_too_long_error(err: BaseException | str) -> bool:
    s = str(err).lower()
    return any(
        x in s
        for x in (
            "413",
            "prompt_too_long",
            "context_length",
            "maximum context",
            "too many tokens",
            "token limit",
            "context window",
            "request too large",
        )
    )


async def reactive_compact_if_needed(
    messages: list[dict[str, Any]],
    *,
    session_id: uuid.UUID | None = None,
    force: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """413 / prompt_too_long 应急压缩：强制 pipeline + 更狠的 tool 截断。"""
    logger.warning("reactiveCompact: forcing emergency compression session=%s", session_id)
    # First pass: hard-trim tool role contents in-place copy
    out: list[dict[str, Any]] = []
    for m in messages:
        mm = dict(m)
        if mm.get("role") == "tool":
            c = mm.get("content")
            if isinstance(c, str) and len(c) > 800:
                mm["content"] = (
                    c[:500]
                    + f"\n...[reactiveCompact omitted {len(c)-700} chars]...\n"
                    + c[-200:]
                )
        out.append(mm)
    # Second: full pipeline at low threshold
    compacted, meta = await compress_history_if_needed(
        out, session_id=session_id, threshold=0.45
    )
    meta = dict(meta or {})
    meta["reactive"] = True
    meta["force"] = force
    return compacted, meta
