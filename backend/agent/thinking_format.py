"""Reasoning / thinking presentation helpers.

Provider-native reasoning_content (Grok, DeepSeek, o-series, …) is streamed and
persisted as <thinking>…</thinking> so the existing frontend ThinkingBlock /
parseMessageContent path can render a collapsible block like Claude / Codex.
"""

from __future__ import annotations

import re
from typing import Optional

# Open/close tags must match frontend parseMessageContent PAIR_TAGS
_THINK_OPEN = "<thinking>"
_THINK_CLOSE = "</thinking>"

_THINK_BLOCK_RE = re.compile(
    r"<thinking\b[^>]*>[\s\S]*?</thinking>"
    r"|<think\b[^>]*>[\s\S]*?</think>"
    r"|\[Thinking\][\s\S]*?\[/Thinking\]"
    r"|【思考】[\s\S]*?【/思考】",
    re.I,
)
_THINK_OPEN_UNCLOSED_RE = re.compile(
    r"(?:<thinking\b[^>]*>|<think\b[^>]*>|\[Thinking\]|【思考】)([\s\S]*)$",
    re.I,
)


def wrap_thinking(reasoning: Optional[str], content: Optional[str]) -> str:
    """Prefix visible content with a closed thinking block when reasoning exists."""
    r = (reasoning or "").strip()
    c = content or ""
    if not r:
        return c
    # Avoid double-wrapping if content already starts with a thinking block
    head = c.lstrip()[:20].lower()
    if head.startswith("<thinking") or head.startswith("<think") or head.startswith("[thinking]"):
        return c
    if c.strip():
        return f"{_THINK_OPEN}\n{r}\n{_THINK_CLOSE}\n\n{c}"
    return f"{_THINK_OPEN}\n{r}\n{_THINK_CLOSE}"


def strip_thinking(text: Optional[str]) -> str:
    """Remove closed (and trailing unclosed) thinking blocks; return visible body."""
    if not text:
        return ""
    s = _THINK_BLOCK_RE.sub("", text)
    s = _THINK_OPEN_UNCLOSED_RE.sub("", s)
    # fenced ```thinking
    s = re.sub(r"```(?:thinking|thought|reasoning)\s*\n[\s\S]*?```", "", s, flags=re.I)
    return s.strip()


def is_visible_empty(text: Optional[str]) -> bool:
    """True when there is no user-visible body after stripping thinking."""
    return not strip_thinking(text)
