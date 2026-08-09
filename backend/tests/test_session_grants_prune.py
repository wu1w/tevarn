"""session_grants: clear on delete path helpers, TTL, orphan prune."""

from __future__ import annotations

import time

import pytest

from backend.agent.grant_store import (
    add_session_grant,
    clear_session_grants,
    has_session_grant,
    prune_expired_session_grants,
    prune_orphan_session_grants,
    reset_for_tests,
)


@pytest.fixture(autouse=True)
def _clear():
    reset_for_tests()
    yield
    reset_for_tests()


def test_clear_session_grants_removes_and_persist():
    sid = "sid-clear-1"
    add_session_grant(sid, "command", {"command": "ls"}, whole_tool=True)
    assert has_session_grant(sid, "command", {"command": "ls"})
    clear_session_grants(sid)
    assert not has_session_grant(sid, "command", {"command": "ls"})


def test_orphan_prune_keeps_live_only():
    add_session_grant("live-a", "file_write", whole_tool=True)
    add_session_grant("dead-b", "file_write", whole_tool=True)
    n = prune_orphan_session_grants({"live-a"})
    assert n == 1
    assert has_session_grant("live-a", "file_write")
    assert not has_session_grant("dead-b", "file_write")


def test_ttl_prune(monkeypatch):
    monkeypatch.setenv("TEVARN_SESSION_GRANT_TTL_SECONDS", "1")
    sid = "ttl-sid"
    add_session_grant(sid, "command", {"command": "echo"}, whole_tool=True)
    assert has_session_grant(sid, "command", {"command": "echo"})
    # force age past TTL
    from backend.agent import grant_store as gs

    with gs._grants_lock:
        gs._session_grant_ts[sid] = time.time() - 10
    n = prune_expired_session_grants()
    assert n >= 1
    assert not has_session_grant(sid, "command", {"command": "echo"})
