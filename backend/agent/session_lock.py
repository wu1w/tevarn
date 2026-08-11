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
            # Never drop a lock that is still held (would allow dual runners)
            old = _session_locks.get(oldest_key)
            if old is not None and old.locked():
                # Map full of live locks — still create a new entry (rare)
                pass
            else:
                del _session_locks[oldest_key]
        _session_locks[session_id] = asyncio.Lock()
    return _session_locks[session_id]


async def acquire_session_lock(
    session_id: uuid.UUID,
    *,
    timeout: float | None = 120.0,
) -> tuple[asyncio.Lock, bool]:
    """Acquire session lock with optional timeout.

    Returns (lock, acquired). If not acquired, caller must not run the session.
    ``timeout<=0`` or None waits forever (legacy).
    """
    lock = get_session_lock(session_id)
    if timeout is None or float(timeout) <= 0:
        await lock.acquire()
        return lock, True
    try:
        await asyncio.wait_for(lock.acquire(), timeout=float(timeout))
        return lock, True
    except asyncio.TimeoutError:
        return lock, False


def remove_session_lock(session_id: uuid.UUID) -> None:
    """Drop the lock after session end (leak prevention)."""
    lock = _session_locks.get(session_id)
    if lock is not None and lock.locked():
        return  # still held — do not drop
    _session_locks.pop(session_id, None)


# Back-compat aliases used by loop / tests
_get_session_lock = get_session_lock
_remove_session_lock = remove_session_lock
