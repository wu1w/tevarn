"""Detect when the user wants decisive execution — not more questions."""

from __future__ import annotations

import re
from typing import Any

# 用户明确要求「别问了、按我说的做」
_DIRECT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.I)
    for p in (
        r"按我的指令",
        r"按我说的",
        r"直接执行",
        r"直接做",
        r"不要问",
        r"别问了",
        r"无需确认",
        r"不用确认",
        r"别再问",
        r"禁止\s*clarify",
        r"不要\s*clarify",
        r"一步一步",  # 用户主导节奏：先做第一步，勿反问
        r"先清理",
        r"先改",
        r"先做",
        r"马上",
        r"立刻",
        r"just do it",
        r"don'?t ask",
        r"no questions",
        r"execute now",
        r"go ahead",
        r"as I said",
        r"follow my instruction",
    )
)


def last_user_text(messages: list[dict[str, Any]] | None) -> str:
    if not messages:
        return ""
    for m in reversed(messages):
        if not isinstance(m, dict):
            continue
        if m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str) and c.strip():
            return c.strip()
        if isinstance(c, list):
            parts: list[str] = []
            for part in c:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text") or ""))
                elif isinstance(part, str):
                    parts.append(part)
            t = "\n".join(parts).strip()
            if t:
                return t
    return ""


def is_direct_execute_intent(text: str | None) -> bool:
    """True when clarify should be suppressed."""
    t = (text or "").strip()
    if not t:
        return False
    # system rollups are not "user direct" — still allow clarify for true ambiguity
    if t.startswith("【系统·编制自动回调】") or t.startswith("【工作任务】"):
        return False
    # short decisive commands
    if any(p.search(t) for p in _DIRECT_PATTERNS):
        return True
    return False


def filter_clarify_from_tools(
    tools: list[dict[str, Any]] | None,
    *,
    user_text: str | None = None,
    messages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]] | None:
    """Drop clarify tool schema when user wants direct execution."""
    if not tools:
        return tools
    text = user_text if user_text is not None else last_user_text(messages)
    if not is_direct_execute_intent(text):
        return tools
    out: list[dict[str, Any]] = []
    for t in tools:
        if not isinstance(t, dict):
            out.append(t)
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else {}
        name = str((fn or {}).get("name") or t.get("name") or "")
        if name == "clarify":
            continue
        out.append(t)
    return out


__all__ = [
    "last_user_text",
    "is_direct_execute_intent",
    "filter_clarify_from_tools",
]
