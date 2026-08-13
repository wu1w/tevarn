"""User-channel presentation: hide thinking / pre-tool essays; wrap-up stop.

Chat persist and the WS stream are the user channel. Provider
``reasoning_content`` / Gemini ``thought_signature`` / ``extra_content`` stay
on the LLM turn (not in ``messages.content`` shown to the user).
"""

from __future__ import annotations

import re
from typing import Optional

from backend.agent.thinking_format import strip_thinking

_PROGRESS_HEAD = re.compile(
    r"^(接下来|我将|我先|先读取|正在|让我|稍等|"
    r"I(?:'ll| will) |Let me |Looking at |Reading )",
    re.I,
)
_DEFINES_PROJECT = re.compile(
    r"(是一个|是一種|is an?\s|written in\s|Rust SOCKS|HTTP 代理)",
    re.I,
)
_COMPLETE_CLOSE = re.compile(
    r"(结论|发现|finding|summary|问题清单|逻辑\s*bug|code\s*review|"
    r"审查结论|建议修复)",
    re.I,
)


def user_visible_content(text: Optional[str]) -> str:
    """Strip thinking/reason tags; this is what the user may see."""
    return strip_thinking(text)


def looks_like_progress_note(text: Optional[str]) -> bool:
    """Short / in-progress narration that may sit next to tool cards."""
    body = user_visible_content(text)
    if not body:
        return True
    # Fabricated "X is a …" blurbs are not progress notes, even if short.
    if _DEFINES_PROJECT.search(body) and not _PROGRESS_HEAD.search(body.lstrip()):
        return False
    if len(body) <= 80:
        return True
    if len(body) <= 160 and _PROGRESS_HEAD.search(body.lstrip()):
        return True
    return False


def looks_like_complete_final_answer(text: Optional[str]) -> bool:
    """Heuristic: a standalone user-facing write-up, not a progress note."""
    body = user_visible_content(text)
    if len(body) < 400:
        return False
    if looks_like_progress_note(body):
        return False
    headings = len(re.findall(r"(?m)^#{1,3}\s+\S", body))
    bullets = len(re.findall(r"(?m)^(?:[-*]|\d+\.)\s+\S", body))
    has_close = bool(_COMPLETE_CLOSE.search(body))
    if headings >= 2 and len(body) >= 400:
        return True
    if headings >= 1 and bullets >= 4 and len(body) >= 400:
        return True
    if has_close and len(body) >= 700:
        return True
    if len(body) >= 1500 and bullets >= 6:
        return True
    return False


def content_for_chat_persist(
    text: Optional[str],
    *,
    has_tool_calls: bool = False,
) -> str:
    """User-visible body stored on the assistant chat row.

    Tool-call turns: keep only short progress notes. Long pre-tool essays
    (fabricated project descriptions, premature reviews) are omitted so they
    never become a user bubble. LLM history still carries the model text
    separately.
    """
    body = user_visible_content(text)
    if not has_tool_calls:
        return body
    if looks_like_progress_note(body):
        return body
    return ""


def should_stop_wrapup_redraft(
    *,
    current: Optional[str],
    previous: Optional[str],
    tool_rounds: int,
) -> bool:
    """True when a complete answer was already produced and this is a re-draft.

    Requires prior evidence tools (``tool_rounds >= 2``) so a first-turn
    hallucination + ``use_tool_pack`` is *not* treated as done.
    """
    if int(tool_rounds or 0) < 2:
        return False
    if not looks_like_complete_final_answer(current):
        return False
    if not looks_like_complete_final_answer(previous):
        return False
    return True


__all__ = [
    "user_visible_content",
    "looks_like_progress_note",
    "looks_like_complete_final_answer",
    "content_for_chat_persist",
    "should_stop_wrapup_redraft",
]
