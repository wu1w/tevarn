"""Short-window user message dedup for WebSocket chat (reconnect / double-tap)."""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# session_id -> (content_hash, monotonic_ts)
_LAST: dict[str, tuple[str, float]] = {}
_MAX_KEYS = 512


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:24]


def should_drop_duplicate_user(session_id: Any, content: str) -> bool:
    """True if same content was accepted for this session within the window."""
    try:
        from backend.core.config import settings

        window = float(getattr(settings, "chat_user_dedup_seconds", 8.0) or 0)
    except Exception:
        window = 8.0
    if window <= 0:
        return False
    text = (content or "").strip()
    if not text:
        return False
    key = str(session_id or "")
    if not key:
        return False
    h = _hash(text)
    now = time.monotonic()
    # prune occasionally
    if len(_LAST) > _MAX_KEYS:
        cutoff = now - max(window * 4, 60.0)
        for k in list(_LAST.keys()):
            if _LAST[k][1] < cutoff:
                _LAST.pop(k, None)

    prev = _LAST.get(key)
    if prev and prev[0] == h and (now - prev[1]) < window:
        return True
    _LAST[key] = (h, now)
    return False
