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
    allow_l5: bool = True,
    micro_only: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    若估算 token 超过阈值预算，运行 pipeline (L1/L3/L5)。

    allow_l5 / micro_only:
      工具轮 mid-loop 应传 allow_l5=False（或 micro_only=True），
      只做 L1/L3，避免 Claude Code 风格的「全量摘要」打断同轮长任务。

    注意：阈值判断用**本调用局部 TokenMeter**，不再改写全局 engine.threshold_percent
   （多 session 并发压缩时避免互相踩阈值）。
    """
    engine = get_context_engine()
    thr = float(threshold or getattr(engine, "threshold_percent", 0.75) or 0.75)
    window = int(getattr(settings, "context_window", 128_000) or 128_000)
    local_meter = TokenMeter(context_window=window, threshold_percent=thr)

    tokens = estimate_msgs_tokens(messages)
    meta_base: dict[str, Any] = {
        "compressed": False,
        "tokens_before": tokens,
        "context_window": window,
        "budget": getattr(local_meter, "threshold_tokens", 0) or 0,
        "threshold_percent": thr,
    }

    # always cheap L1; full compress when over threshold or preflight
    over = local_meter.should_compress(tokens) or engine.should_compress_preflight(messages)
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
        messages,
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
