"""Per-session asyncio locks — extracted from the agent loop conductor.

Prevents concurrent agent loops on the same session from racing.
"""

from __future__ import annotations

import asyncio
import uuid

_session_locks: dict[uuid.UUID, asyncio.Lock] = {}
_SESSION_LOCK_MAX = 1024  # bound memory: max retained locks


def get_session_lock(session_id: uuid.UUID) -> asyncio.Lock:
    """Return the session-scoped execution lock."""
    if session_id not in _session_locks:
        if len(_session_locks) >= _SESSION_LOCK_MAX:
            oldest_key = next(iter(_session_locks))
            del _session_locks[oldest_key]
        _session_locks[session_id] = asyncio.Lock()
    return _session_locks[session_id]


def remove_session_lock(session_id: uuid.UUID) -> None:
    """Drop the lock after session end (leak prevention)."""
    _session_locks.pop(session_id, None)


# Back-compat aliases used by loop / tests
_get_session_lock = get_session_lock
_remove_session_lock = remove_session_lock
