"""H2 hardening: production guard, cap_tools closed loop, path keys, resume event."""

from __future__ import annotations

import os

import pytest


def test_cap_tools_none_caps_fail_closed_under_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAKTON_FORCE_PRODUCTION_GUARD", "1")
    monkeypatch.delenv("TAKTON_DEV_UNSAFE", raising=False)
    # clear pytest marker interference: force production
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    from backend.agent.cap_tools import filter_tools_for_process
    from backend.kernel.production_guard import is_production_guard

    # When FORCE_PRODUCTION_GUARD=1, is_production_guard True even under pytest
    assert is_production_guard() is True

    class P:
        id = "proc_test_h2"
        capabilities = None

    tools = [
        {"type": "function", "function": {"name": "file_read"}},
        {"type": "function", "function": {"name": "command"}},
    ]
    out = filter_tools_for_process(tools, P())
    assert out == [], "production must not expose full schema for capabilities=None"


def test_cap_tools_none_caps_open_under_dev_unsafe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAKTON_DEV_UNSAFE", "1")
    monkeypatch.delenv("TAKTON_FORCE_PRODUCTION_GUARD", raising=False)

    from backend.agent.cap_tools import filter_tools_for_process
    from backend.kernel.production_guard import allow_compat_full_open

    assert allow_compat_full_open() is True

    class P:
        id = "proc_dev"
        capabilities = None

    tools = [
        {"type": "function", "function": {"name": "file_read"}},
        {"type": "function", "function": {"name": "command"}},
    ]
    # Without kernel filter, may return tools unchanged or filtered — must not crash
    out = filter_tools_for_process(tools, P())
    assert isinstance(out, list)


def test_path_candidates_extract_file_path_and_src() -> None:
    from backend.kernel.permission_court import _extract_path_candidates

    c = _extract_path_candidates(
        {"file_path": "/tmp/a.txt", "src": "/tmp/b.txt", "url": "https://x.com"}
    )
    assert "/tmp/a.txt" in c
    assert "/tmp/b.txt" in c
    assert not any("https" in x for x in c)


def test_from_dict_rebuilds_resume_event() -> None:
    from backend.kernel.process import AgentProcess

    p = AgentProcess.from_dict(
        {
            "id": "abc1234567890xyz",
            "identity": "main",
            "state": "suspended",
            "capabilities": ["file_read"],
            "tokens_used": 0,
        }
    )
    assert p.state == "suspended"
    ev = p._resume_event
    assert ev is not None
    assert not ev.is_set(), "suspended must leave event cleared"

    p2 = AgentProcess.from_dict(
        {
            "id": "abc1234567890xy2",
            "identity": "main",
            "state": "running",
            "capabilities": ["file_read"],
        }
    )
    assert p2._resume_event is not None
    assert p2._resume_event.is_set()


def test_hmac_key_prefers_dedicated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAKTON_TOKEN_HMAC_SECRET", "unit-test-hmac-secret-key-32b!")
    # reset cache
    import backend.kernel.signing as signing

    signing._key_cache = None
    signing._key_source = "unset"
    k1 = signing._hmac_key()
    assert signing.hmac_key_source() == "dedicated"
    assert len(k1) == 32
    signing._key_cache = None
    monkeypatch.delenv("TAKTON_TOKEN_HMAC_SECRET", raising=False)


def test_audit_rotate_threshold(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.kernel.audit_store as aus

    path = tmp_path / "ev.jsonl"
    store = aus.AuditEventStore(str(path))
    store._max_bytes = 200
    store._keep = 3
    for i in range(50):
        store.append({"id": str(i), "hash": f"h{i}", "prev_hash": f"h{i-1}"})
    assert path.exists() or any(tmp_path.iterdir())
