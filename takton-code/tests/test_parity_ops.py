"""Tests for parity phases A–E helpers."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from takton_code.agent.permissions import PermissionGate, PermissionBroker, rules_for_profile
from takton_code.leader.protocol import decode_line, encode, hello_ok
from takton_code.tui.renderer import format_event_lines
from takton_code.tui.stream_buffer import StreamBuffer


def test_format_text_delta():
    lines = format_event_lines({"type": "text_delta", "text": "hi"})
    assert lines and lines[0].text == "hi" and lines[0].sticky


def test_format_tool_part():
    lines = format_event_lines(
        {
            "type": "part",
            "part": {
                "type": "tool",
                "tool": "grep",
                "state": {"status": "completed", "output": "ok"},
            },
        }
    )
    assert any("grep" in ln.text for ln in lines)


def test_stream_buffer_flush_chars():
    b = StreamBuffer(flush_chars=3, flush_ms=10_000)
    assert b.push("a") is None
    assert b.push("b") is None
    out = b.push("c")
    assert out == "abc"
    assert b.flush() == ""


def test_permission_last_match():
    g = PermissionGate(profile="cautious", mode="build", rules=rules_for_profile("cautious"))
    assert g.check("run_shell", {"command": "ls"}) == "ask"
    assert g.check("edit_file", {"path": "a.py"}) == "allow"
    g.mode = "plan"
    assert g.check("edit_file", {"path": "a.py"}) == "deny"
    # Grok pit: always-approve does NOT unlock plan edits
    g.mode = "plan"
    g.profile = "always"
    g.rules = rules_for_profile("always")
    assert g.check("edit_file", {"path": "a.py"}) == "deny"
    g.mode = "build"
    g.profile = "always"
    g.rules = rules_for_profile("always")
    assert g.check("run_shell", {}) == "allow"


def test_permission_env_and_external(tmp_path: Path):
    g = PermissionGate(
        profile="cautious",
        mode="build",
        project_root=tmp_path,
        rules=rules_for_profile("cautious"),
    )
    assert g.check("file_read", {"path": ".env"}) == "ask"
    assert g.check("file_read", {"path": "src/a.py"}) == "allow"
    # outside root
    assert g.check("file_read", {"path": str(Path.home() / "secret.txt")}) == "ask"


def test_dont_ask_profile():
    g = PermissionGate(profile="dontAsk", mode="build", rules=rules_for_profile("dontAsk"))
    assert g.check("run_shell", {"command": "rm -rf /"}) == "deny"
    assert g.check("edit_file", {"path": "a.py"}) == "allow"


@pytest.mark.asyncio
async def test_permission_broker_reply():
    g = PermissionGate(profile="cautious", mode="build", rules=rules_for_profile("cautious"))
    events = []

    def emit(typ, **kw):
        events.append((typ, kw))

    broker = PermissionBroker(g, emit=emit, timeout_sec=2, headless=False)

    async def answer_soon():
        await asyncio.sleep(0.05)
        assert broker.pending
        rid = next(iter(broker.pending))
        broker.answer(rid, "allow")

    asyncio.create_task(answer_soon())
    dec = await broker.require("run_shell", {"command": "echo hi"})
    assert dec == "allow"
    assert any(t == "permission_request" for t, _ in events)


@pytest.mark.asyncio
async def test_permission_broker_headless_no_block():
    g = PermissionGate(profile="cautious", mode="build", rules=rules_for_profile("cautious"))
    broker = PermissionBroker(g, headless=True, timeout_sec=1)
    # would be ask interactively — headless cancels immediately
    dec = await broker.require("run_shell", {"command": "echo hi"})
    assert dec == "deny"


def test_leader_protocol_roundtrip():
    raw = encode(hello_ok(sessions=[{"id": "1"}]))
    msg = decode_line(raw)
    assert msg and msg["op"] == "hello_ok"
    assert msg["sessions"][0]["id"] == "1"


@pytest.mark.asyncio
async def test_session_hub(tmp_path: Path):
    from takton_code.session.hub import SessionHub
    from takton_code.session.store import SessionStore
    from takton_code.agent.loop import AgentRuntime
    from takton_code.project.binder import bind_project
    from takton_code.llm.provider import LLMProvider, LLMResponse
    from takton_code.config import LLMSettings, AgentSettings

    class DummyLLM(LLMProvider):
        async def chat(self, messages, tools=None, **kw):
            return LLMResponse(content="ok", finish_reason="stop")

    store = SessionStore(tmp_path / "s.db")
    await store.open()
    project = bind_project(tmp_path)
    hub = SessionHub(store)

    async def make_rt():
        rt = AgentRuntime(
            settings_llm=LLMSettings(),
            settings_agent=AgentSettings(permission_profile="free"),
            project=project,
            store=store,
            llm=DummyLLM(),
            mode="build",
            headless=True,
        )
        await rt.setup()
        return rt

    r1 = await make_rt()
    await hub.register(r1)
    r2 = await make_rt()
    await hub.register(r2)
    assert hub.active_id == r2.session_id
    await hub.switch(r1.session_id or "")
    assert hub.active_id == r1.session_id
    rows = await hub.list_db_sessions()
    assert len(rows) >= 2
    await hub.close_all()
    await store.close()


def test_score_bon():
    from takton_code.agent.best_of_n import BonCandidate, score_candidate

    a = BonCandidate(index=0, final_text="x", test_ok=True, changes_summary="modify a.py")
    b = BonCandidate(index=1, final_text="y", error="fail")
    score_candidate(a)
    score_candidate(b)
    assert a.score > b.score


def test_ui_settings():
    from takton_code.config import Settings, UISettings

    s = Settings()
    assert s.ui.screen_mode == "fullscreen"
    assert s.ui.stream_flush_chars == 1
