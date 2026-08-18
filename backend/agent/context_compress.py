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
    threshold: float = COMPRESS_THRESHOLD,  # 单点常量 COMPRESS_THRESHOLD
    allow_l5: bool = True,
    micro_only: bool = False,
    aggressive_hard_drop: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    若估算 token 超过阈值预算，运行 pipeline (L1/L3/L5)。

    allow_l5 / micro_only:
      工具轮 mid-loop 应传 allow_l5=False（或 micro_only=True），
      只做 L1/L3，避免 Claude Code 风格的「全量摘要」打断同轮长任务。

    aggressive_hard_drop:
      When True (default mid-loop), message-count hard path may keep only
      head+tail (~10 msgs). Pre-build history load must pass False — otherwise
      3000+ tool rows collapse to ~5 and the model loses the whole session
      (seen as Codex empty-input / 400 + amnesiac turns).

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
    meta_base: dict[str, Any] = {
        "compressed": False,
        "tokens_before": tokens,
        "context_window": window,
        "budget": getattr(local_meter, "threshold_tokens", 0) or 0,
        "threshold_percent": thr,
        "message_count": n_msg,
    }

    # 消息条数过多：强制压缩（不等 token 阈值）
    # audit-fix(#11)：条数压力统计排除 system nudge——运行时注入的提示消息
    # （tool_round 的 timid/thrash/goal 提示等都是非首条 system），它们会
    # 虚增条数导致过早触发 L5 深度压缩。特征：role=='system' 且非首条。
    n_effective = sum(
        1
        for i, m in enumerate(messages)
        if not (isinstance(m, dict) and m.get("role") == "system" and i > 0)
    )
    count_pressure = n_effective >= soft_n
    if count_pressure and not micro_only:
        # 长会话优先允许 L5；硬上限时强制 allow
        allow_l5 = True
        # audit-fix(#1)：深度阈值 COMPRESS_THRESHOLD_DEEP
        thr = min(thr, COMPRESS_THRESHOLD_DEEP)
        local_meter = TokenMeter(context_window=window, threshold_percent=thr)
        meta_base["count_pressure"] = True
        meta_base["threshold_percent"] = thr

    # always cheap L1; full compress when over threshold or preflight
    over = (
        local_meter.should_compress(tokens)
        or engine.should_compress_preflight(messages)
        or count_pressure
        or n_msg >= hard_n
    )
    if not over and n_msg < 8:
        # still run L1 only for oversized tool blobs
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

    # 超硬上限：必须先尝试真摘要/L3，禁止无 summary 静默砍中段（失忆主因之一）
    work = [dict(m) for m in messages] if messages else []
    if n_msg >= hard_n and not micro_only:
        try:
            # Force a real compress pass first (L1/L3 + L5 if under threshold relax)
            pre_out, pre_meta = await engine.compress(
                work,
                current_tokens=tokens,
                session_id=session_id,
                allow_l5=True,
                micro_only=False,
            )
            meta_base["pre_hard_compress"] = pre_meta
            work = pre_out
            tokens = estimate_msgs_tokens(work)
        except Exception as e:
            logger.debug("pre-hard compress failed: %s", e)

    # Aggressive head/tail hard drop (~10 msgs) is mid-loop emergency only.
    # Pre-build / long-session load must NOT do this — it caused 3538→5 amnesia.
    if (
        aggressive_hard_drop
        and n_msg >= hard_n
        and hasattr(engine, "_hard_truncate")
    ):
        try:
            # Only hard-drop if still extremely bloated after L1/L3/L5
            if len(work) >= max(hard_n * 2, 120):
                old_tail = getattr(engine, "protect_last_n", 8)
                try:
                    # Keep more tail than before (was 6 → near-total amnesia)
                    engine.protect_last_n = max(int(old_tail), 24)  # type: ignore[attr-defined]
                    work, n_drop = engine._hard_truncate(work)  # type: ignore[attr-defined]
                finally:
                    engine.protect_last_n = old_tail  # type: ignore[attr-defined]
                if n_drop:
                    meta_base["pre_hard_drop"] = n_drop
                    tokens = estimate_msgs_tokens(work)
                    logger.info(
                        "pre_hard_drop n=%s after summary attempt session=%s kept=%s",
                        n_drop,
                        session_id,
                        len(work),
                    )
        except Exception:
            pass
    elif not aggressive_hard_drop and len(work) >= max(hard_n * 3, 200):
        # Soft pre-build floor: keep last N tool-heavy turns, pin dropped user asks
        try:
            soft_keep = max(hard_n, int(getattr(settings, "context_max_messages_hard", 72) or 72) * 2)
            soft_keep = min(soft_keep, 160)
            if len(work) > soft_keep:
                dropped = work[:-soft_keep]
                pins: list[str] = []
                for m in dropped:
                    if isinstance(m, dict) and m.get("role") == "user":
                        c = str(m.get("content") or "").strip()
                        if c and not c.startswith("【") and len(pins) < 6:
                            pins.append(" ".join(c.split())[:120])
                pin = (" Prior asks: " + " | ".join(pins)) if pins else ""
                marker = {
                    "role": "system",
                    "content": (
                        f"[pre-build soft trim: dropped {len(dropped)} older msgs; "
                        f"kept last {soft_keep}.{pin}]"
                    ),
                }
                work = [marker] + work[-soft_keep:]
                meta_base["pre_build_soft_trim"] = len(dropped)
                tokens = estimate_msgs_tokens(work)
                logger.info(
                    "pre_build_soft_trim dropped=%s kept=%s session=%s",
                    len(dropped),
                    len(work),
                    session_id,
                )
        except Exception as e:
            logger.debug("pre_build soft trim skip: %s", e)

    # Message-count pressure: lower engine threshold for THIS call so L5 can fire
    # on high-cardinality short-tool sessions (3–6k tokens, 90+ msgs).
    _old_thr = None
    if count_pressure and not micro_only and hasattr(engine, "threshold_percent"):
        try:
            _old_thr = float(engine.threshold_percent)
            # Dual gate: allow L5 from ~35% of window when message-bloated
            engine.threshold_percent = min(_old_thr, 0.35)
            if hasattr(engine, "meter") and engine.meter is not None:
                engine.meter.threshold_percent = engine.threshold_percent
        except Exception:
            _old_thr = None

    try:
        out, meta = await engine.compress(
            work,
            current_tokens=tokens,
            session_id=session_id,
            allow_l5=allow_l5,
            micro_only=micro_only,
        )
    finally:
        if _old_thr is not None:
            try:
                engine.threshold_percent = _old_thr
                if hasattr(engine, "meter") and engine.meter is not None:
                    engine.meter.threshold_percent = _old_thr
            except Exception:
                pass
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
