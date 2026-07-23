"""Normalize tool results before they re-enter the LLM context."""
from __future__ import annotations

from typing import Any


def normalize_tool_result(
    result: Any,
    *,
    max_chars: int = 12_000,
    tool_name: str = "",
) -> str:
    """Coerce to str, apply length cap, preserve error prefixes."""
    if result is None:
        text = ""
    elif isinstance(result, str):
        text = result
    else:
        try:
            text = str(result)
        except Exception:
            text = f"[Error] tool {tool_name or '?'} returned non-string unprintable result"

    if not text:
        text = f"[Error] Tool '{tool_name or '?'}' returned empty result"

    max_chars = max(500, int(max_chars or 12_000))
    if len(text) > max_chars:
        text = (
            text[:max_chars]
            + f"\n\n[截断: 结果超过 {max_chars} 字符 — tool={tool_name or '?'}]"
        )
    return text


def is_tool_error(result: str | None) -> bool:
    t = (result or "").lstrip()
    return t.startswith("[Error]") or t.startswith("[error]") or t.startswith("[Security")


__all__ = ["normalize_tool_result", "is_tool_error"]
