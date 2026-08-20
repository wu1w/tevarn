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
# Mid-body progress (models often lead with "官网已定位" then "接着抽…").
_PROGRESS_BODY = re.compile(
    r"(接着抽|接着读|再核一下|避免只凭|先分页读|正在拉正文|正文再核|"
    r"不要停在一句进度|再给你结论|先定位|不再重复搜)",
)
# Model sometimes wraps the same sentence in a fake file protocol
# (`<file_start>…<file_end>` — not necessarily a slash close tag).
_FILE_WRAP = re.compile(r"<file_start>[\s\S]*?</?file_end>", re.I)
_FILE_TAG = re.compile(r"</?file_(?:start|end)>", re.I)
_DEFINES_PROJECT = re.compile(
    r"(是一个|是一種|is an?\s|written in\s|Rust SOCKS|HTTP 代理)",
    re.I,
)
_COMPLETE_CLOSE = re.compile(
    r"(结论|发现|finding|summary|问题清单|逻辑\s*bug|code\s*review|"
    r"审查结论|建议修复)",
    re.I,
)
# Model stop tokens that sometimes leak into the assistant body.
_LEAKED_STOP = re.compile(
    r"\s*<\|(?:eos|endoftext|im_end|eot_id)\|>\s*",
    re.I,
)
_UNIQUE_WRAP = re.compile(
    r"(?is)<\|uniquecall_id\|>[\s\S]*?(?:</uniquecall>|$)",
)
_UNIQUE_TAG = re.compile(
    r"</?unique[A-Za-z_][\w]*>|<\|unique[A-Za-z_][\w]*\|>",
    re.I,
)


def _strip_leaked_stop_tokens(body: str) -> str:
    """Remove leaked stop tokens and the whitespace hugging them."""
    if not body:
        return body or ""
    out = _FILE_WRAP.sub("", body)
    out = _FILE_TAG.sub("", out)
    out = _UNIQUE_WRAP.sub("", out)
    out = _UNIQUE_TAG.sub("", out)
    out = re.sub(r"(?i)</uniquecall>?", "", out)
    out = _LEAKED_STOP.sub(" ", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def user_visible_content(text: Optional[str]) -> str:
    """Strip thinking/reason tags; this is what the user may see."""
    body = strip_thinking(text)
    # remaining <think / <thinking / </think are mentions, not real blocks
    # (old frontend parseMessageContent treats leftover openers as unclosed thinking)
    body = re.sub(r"<(?=/?(?:think|thinking)\b)", "&lt;", body, flags=re.I)
    try:
        from backend.agent.pseudo_tool_recover import collapse_repetition_tail
        from backend.agent.pseudo_tool_recover import looks_like_pseudo_tool_content
        from backend.agent.pseudo_tool_recover import scrub_leak_markers

        if looks_like_pseudo_tool_content(body):
            body = scrub_leak_markers(body)
        else:
            body = collapse_repetition_tail(body)
    except Exception:
        pass
    return _strip_leaked_stop_tokens(body)


def looks_like_in_progress_narration(text: Optional[str]) -> bool:
    """True only for 'I will now…' / '接着抽…' asides, not every short answer.

    Used to block finalize. ``looks_like_progress_note`` stays looser so
    tool-turn persist can keep a short status line.
    """
    body = user_visible_content(text)
    if not body:
        return False
    if _PROGRESS_HEAD.search(body.lstrip()):
        return True
    if _PROGRESS_BODY.search(body):
        return True
    return False


def looks_like_progress_note(text: Optional[str]) -> bool:
    """Short / in-progress narration that may sit next to tool cards."""
    body = user_visible_content(text)
    if not body:
        return True
    # Fabricated "X is a …" blurbs are not progress notes, even if short.
    if _DEFINES_PROJECT.search(body) and not _PROGRESS_HEAD.search(body.lstrip()):
        return False
    if looks_like_in_progress_narration(body):
        return True
    if len(body) <= 80:
        return True
    if len(body) <= 160 and _PROGRESS_HEAD.search(body.lstrip()):
        return True
    if len(body) <= 220 and _PROGRESS_BODY.search(body):
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
    "looks_like_in_progress_narration",
    "looks_like_complete_final_answer",
    "content_for_chat_persist",
    "should_stop_wrapup_redraft",
]
