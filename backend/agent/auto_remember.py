"""Lightweight end-of-turn memory capture (no LLM call).

Writes high-signal decisions into memory_graph so long sessions don't rely
solely on raw history. Safe defaults: skip empty/short, de-dupe by title.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# Product / stack / naming decisions users care about across turns
_SIGNAL = re.compile(
    r"(?:"
    r"技术栈|tech\s*stack|选用|采用|正式名|产品名|代号|codename|"
    r"PRD|规格|architecture|权限|vault|PAM|hamvor|tevarn\s*guard|"
    r"决定|确定|不要|先做|后续|方案"
    r")",
    re.I,
)


def _should_remember(user_input: str, final_content: str) -> bool:
    u = (user_input or "").strip()
    f = (final_content or "").strip()
    if len(u) < 12 and len(f) < 40:
        return False
    if f.startswith("[Error]") or f.startswith("[提示]"):
        return False
    blob = f"{u}\n{f}"
    if _SIGNAL.search(blob):
        return True
    # Long substantive answers still worth a short note
    return len(f) >= 400 and len(u) >= 20


def _title_from(user_input: str) -> str:
    t = re.sub(r"\s+", " ", (user_input or "").strip())[:80]
    return t or "session note"


async def maybe_auto_remember(
    *,
    user_input: str,
    final_content: str,
    user_id: Any = None,
    session_id: Any = None,
    run_id: Any = None,
) -> str | None:
    """Return memory id string if written, else None."""
    if not _should_remember(user_input, final_content):
        return None
    try:
        from backend.core.config import settings

        if not bool(getattr(settings, "memory_auto_remember_chat", True)):
            return None
    except Exception:
        pass

    uid = None
    if user_id:
        try:
            uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
        except Exception:
            uid = None

    title = _title_from(user_input)
    body = (
        f"用户: {(user_input or '')[:400]}\n"
        f"助手要点: {(final_content or '')[:1200]}"
    )
    try:
        from backend.services import memory_bus

        result = await memory_bus.remember(
            "decision",
            body,
            title=title[:200],
            tags=["auto", "chat"],
            user_id=uid,
            source_run_id=str(run_id) if run_id else None,
            confidence=0.7,
            meta={"session_id": str(session_id or "") or None, "auto": True},
            source="auto_remember",
        )
        if result.ok and result.id:
            logger.info("auto_remember ok id=%s title=%s", result.id, title[:60])
            return str(result.id)
        logger.debug("auto_remember skip: %s", getattr(result, "message", ""))
    except Exception as e:
        logger.debug("auto_remember failed: %s", e)
    return None
