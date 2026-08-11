"""Session lock wait timeout + acquire helper."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from backend.agent.session_lock import (
    acquire_session_lock,
    get_session_lock,
    remove_session_lock,
)


@pytest.mark.asyncio
async def test_acquire_session_lock_timeout():
    sid = uuid.uuid4()
    lock = get_session_lock(sid)
    await lock.acquire()
    try:
        _lock2, ok = await acquire_session_lock(sid, timeout=0.15)
        assert ok is False
        assert _lock2 is lock
    finally:
        lock.release()
    remove_session_lock(sid)


@pytest.mark.asyncio
async def test_acquire_session_lock_ok():
    sid = uuid.uuid4()
    lock, ok = await acquire_session_lock(sid, timeout=1.0)
    assert ok is True
    assert lock.locked()
    lock.release()
    remove_session_lock(sid)
