"""Thin vague-work detection for underspecified coding asks.

Industry pattern (Codex/Claude/Cursor): default bias-to-action, but when the
deliverable is underspecified, prefer structured clarify (MCQ) over inventing
a large multi-phase scope. This module is prologue-only policy text + helpers —
it does **not** add mid-loop phases.
"""

from __future__ import annotations

import re

from backend.agent.direct_intent import is_direct_execute_intent
from backend.agent.robust import is_continue_phrase

# Short underspecified work asks — not plan/read Q&A, not explicit team dispatch.
_VAGUE_WORK_RE = re.compile(
    r"(?i)^[\s\u3000]{0,8}(?:"
    r"(?:帮我)?(?:弄好|弄一下|搞定|处理好|改好|修一下|完善一下)|"
    r"你看着办|看着处理|随便改改|随便弄|"
    r"fix\s*(?:it|this)|make\s+it\s+work|just\s+fix\s+it|"
    r"帮我处理一下|处理一下吧"
    r")[\s!！。.~～…]*$"
)

# Slightly longer but still no concrete deliverable / path / file
_VAGUE_WORK_LOOSE_RE = re.compile(
    r"(?i)^[\s\u3000]{0,8}(?:"
    r"帮我(?:弄好|搞定|处理好|弄一下).{0,12}|"
    r"(?:把|将).{0,16}(?:弄好|搞定|处理好)"
    r")[\s!！。.~～…]*$"
)

# Concrete anchors → not vague (path, feature name, explicit verb+object)
_CONCRETE_ANCHOR_RE = re.compile(
    r"(?i)("
    r"[A-Za-z]:\\|/[\w.\-]+|"  # path
    r"\.(?:rs|py|ts|tsx|go|md|toml|json)\b|"
    r"guardian-|crate|cargo|bootstrap|migrate|"
    r"登录|认证|路由|编译|测试|PRD|SPEC|文档|"
    r"fix\s+bug|implement|refactor"
    r")"
)

VAGUE_NOTE_MARKER = "【模糊范围·ephemeral】"


def is_vague_work_intent(text: str | None) -> bool:
    """True when the user asks to 'just fix/handle it' without a clear slice."""
    t = (text or "").strip()
    if not t or len(t) > 64:
        return False
    if t.startswith("【系统") or t.startswith("[System") or t.startswith("【工作任务】"):
        return False
    if is_direct_execute_intent(t):
        return False
    # Continue phrases have their own resume path — not "vague invent scope"
    if is_continue_phrase(t):
        return False
    if _CONCRETE_ANCHOR_RE.search(t):
        return False
    if _VAGUE_WORK_RE.match(t):
        return True
    if len(t) <= 40 and _VAGUE_WORK_LOOSE_RE.match(t):
        return True
    return False


def vague_work_system_note() -> str:
    """Soft policy only — model may skip clarify if history already defines the slice."""
    return (
        f"{VAGUE_NOTE_MARKER} Scope is underspecified. "
        "Before a large multi-file or multi-phase change: call **clarify** with "
        "1–3 multiple-choice questions (include a recommended option) about "
        "which next slice to do and what 'done' means. "
        "Do **not** invent a broad new project or hire crew until the user picks. "
        "If the next slice is already clear from the conversation or an active Goal, "
        "skip clarify and do **only that one slice**. "
        "Reply in the user's language."
    )


def is_ephemeral_vague_note(content: str | None) -> bool:
    return bool(content and VAGUE_NOTE_MARKER in str(content))


__all__ = [
    "VAGUE_NOTE_MARKER",
    "is_vague_work_intent",
    "vague_work_system_note",
    "is_ephemeral_vague_note",
]
