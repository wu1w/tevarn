"""
会话上下文压缩：兼容入口。

实现已迁移到 ContextPipeline (L1/L3/L5)。本模块保留
`compress_history_if_needed` 与 `estimate_msgs_tokens` 给旧调用方。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from backend.agent.context_engine import get_context_engine
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
    threshold: float = 0.75,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    若估算 token 超过阈值预算，运行 pipeline (L1/L3/L5)。
    """
    engine = get_context_engine()
    # allow caller threshold override for this call
    old = engine.threshold_percent
    try:
        engine.threshold_percent = float(threshold or old)
        if hasattr(engine, "meter"):
            engine.meter.threshold_percent = engine.threshold_percent

        tokens = estimate_msgs_tokens(messages)
        meta_base: dict[str, Any] = {
            "compressed": False,
            "tokens_before": tokens,
            "context_window": int(getattr(settings, "context_window", 128_000) or 128_000),
            "budget": engine.meter.threshold_tokens if hasattr(engine, "meter") else 0,
        }

        # always cheap L1; full compress when over threshold or preflight
        over = engine.should_compress(tokens) or engine.should_compress_preflight(messages)
        if not over and len(messages) < 8:
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

        out, meta = await engine.compress(
            messages, current_tokens=tokens, session_id=session_id
        )
        meta_base.update(meta)
        return out, meta_base
    finally:
        engine.threshold_percent = old
        if hasattr(engine, "meter"):
            engine.meter.threshold_percent = old


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
