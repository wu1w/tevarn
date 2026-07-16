"""
会话上下文压缩：接近 context_window 时对旧消息做 LLM 滚动摘要。
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from backend.core.config import settings

logger = logging.getLogger(__name__)


def estimate_msgs_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            total += max(8, round(len(c) / 3.4))
        elif c is None:
            total += 4
        # tool_calls 粗估
        tcs = m.get("tool_calls")
        if tcs:
            total += 80 * len(tcs)
    return total


async def compress_history_if_needed(
    messages: list[dict[str, Any]],
    *,
    session_id: uuid.UUID | None = None,
    threshold: float = 0.75,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    若估算 token 超过 context_window * threshold，将中间历史压缩为一条摘要。
    保留 system + 最近若干轮 user/assistant/tool。
    """
    ctx_window = int(getattr(settings, "context_window", 128_000) or 128_000)
    # 预留生成与 system 空间
    budget = max(4_000, int(ctx_window * threshold) - int(getattr(settings, "default_max_tokens", 12_288) or 12_288))
    tokens = estimate_msgs_tokens(messages)
    meta: dict[str, Any] = {
        "compressed": False,
        "tokens_before": tokens,
        "context_window": ctx_window,
        "budget": budget,
    }
    if tokens <= budget or len(messages) < 8:
        return messages, meta

    # 拆分 system / 其余
    systems = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    if len(rest) < 6:
        return messages, meta

    # 保留最近 N 条
    keep_tail = 12
    head = rest[:-keep_tail]
    tail = rest[-keep_tail:]
    if not head:
        return messages, meta

    # 构造摘要输入
    transcript_lines = []
    for m in head:
        role = m.get("role", "?")
        content = m.get("content") or ""
        if isinstance(content, str) and content.strip():
            transcript_lines.append(f"{role}: {content[:2000]}")
    transcript = "\n".join(transcript_lines)
    if len(transcript) > 40_000:
        transcript = transcript[:40_000] + "\n…[truncated]"

    summary_text = ""
    try:
        from backend.services.llm import LLMServiceFactory

        llm = LLMServiceFactory.get_service()
        prompt = [
            {
                "role": "system",
                "content": (
                    "你是会话压缩助手。将下列历史对话压缩为简洁的中文要点摘要，"
                    "保留目标、已完成事项、关键事实、未决问题与约束。不要编造。"
                    "输出纯文本，200-600 字。"
                ),
            },
            {"role": "user", "content": transcript},
        ]
        parts: list[str] = []
        async for chunk in llm.chat(prompt, tools=None, stream=False):
            if chunk.delta:
                parts.append(chunk.delta)
        summary_text = "".join(parts).strip()
    except Exception as e:
        logger.warning("LLM compress failed, fallback truncate: %s", e)
        summary_text = (
            f"[历史已截断，共 {len(head)} 条消息被省略；请基于最近对话继续。]"
        )

    if not summary_text:
        summary_text = f"[历史已压缩：省略较早 {len(head)} 条消息]"

    # 可选：写入 CtxItem
    if session_id is not None:
        try:
            from backend.repositories.context_repo import AsyncCtxItemRepository

            repo = AsyncCtxItemRepository()
            await repo.create(
                {
                    "session_id": session_id,
                    "scope": "session",
                    "kind": "memory",
                    "key": f"summary_{int(uuid.uuid4().int % 1e12)}",
                    "value": summary_text,
                    "tokens": max(8, round(len(summary_text) / 3.4)),
                    "pinned": True,
                    "ttl": "session",
                    "origin": "context_compress",
                }
            )
        except Exception as e:
            logger.debug("save summary ctx failed: %s", e)

    summary_msg = {
        "role": "system",
        "content": f"# 对话历史摘要（已压缩）\n{summary_text}",
    }
    new_messages = systems + [summary_msg] + tail
    meta.update(
        {
            "compressed": True,
            "tokens_after": estimate_msgs_tokens(new_messages),
            "dropped_messages": len(head),
        }
    )
    logger.info(
        "Context compressed: %s → %s tokens (dropped %s msgs)",
        tokens,
        meta["tokens_after"],
        len(head),
    )
    return new_messages, meta
