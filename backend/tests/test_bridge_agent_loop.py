"""Event sink alignment + bridge agent turn + TUI bridge loop flag."""
from __future__ import annotations

from uuid import uuid4

import pytest

from backend.integrations.websocket_event_sink import WebSocketEventSink


class _FakeWS:
    def __init__(self) -> None:
        self.msgs: list[tuple] = []

    async def broadcast(self, session_id, message):
        self.msgs.append((session_id, message))


@pytest.mark.asyncio
async def test_event_sink_status_shape():
    ws = _FakeWS()
    sink = WebSocketEventSink(ws_manager=ws)
    sid = uuid4()
    await sink.push_status(sid, "thinking", "hi")
    assert ws.msgs
    _sid, payload = ws.msgs[0]
    assert _sid == sid
    assert payload["type"] == "status"
    assert payload["state"] == "thinking"
    assert payload.get("detail") == "hi"


@pytest.mark.asyncio
async def test_event_sink_stream_shape():
    ws = _FakeWS()
    sink = WebSocketEventSink(ws_manager=ws)
    sid = uuid4()
    mid = uuid4()
    await sink.push_stream_delta(sid, "tok", message_id=mid)
    _sid, payload = ws.msgs[0]
    assert payload["type"] == "stream_delta"
    assert payload["content"] == "tok"
    assert str(payload["message_id"]) == str(mid)


@pytest.mark.asyncio
async def test_loop_push_uses_event_sink():
    from backend.agent.loop import NexusAgentLoop

    ws = _FakeWS()
    sink = WebSocketEventSink(ws_manager=ws)
    loop = NexusAgentLoop.__new__(NexusAgentLoop)
    loop.ws_manager = None
    loop.event_sink = sink
    loop.progress_sink = None
    sid = uuid4()
    await loop._push_status(sid, "idle", "Ready")
    assert ws.msgs and ws.msgs[0][1]["state"] == "idle"


def test_agent_turn_request_model():
    # takton-code 为独立 repo（跨仓库契约测试）：本机无 checkout 时 skip 而非 fail
    pytest.importorskip("takton_code.bridge.protocol", reason="takton-code repo not checked out")
    from takton_code.bridge.protocol import BRIDGE_ROUTES, AgentTurnRequest

    assert "agent_turn" in BRIDGE_ROUTES
    r = AgentTurnRequest(message="hi", mode="build", project_root="/tmp")
    assert r.message == "hi"


@pytest.mark.asyncio
async def test_runtime_bridge_turn_mock():
    from dataclasses import dataclass

    pytest.importorskip("takton_code.agent.loop", reason="takton-code repo not checked out")
    from takton_code.agent.loop import AgentRuntime, TurnResult
    from takton_code.bridge.protocol import AgentTurnResult

    @dataclass
    class FakeBridge:
        enabled: bool = True

        async def health(self):
            return {"ok": True}

        async def agent_turn(self, req):
            return AgentTurnResult(
                ok=True, session_id="11111111-1111-1111-1111-111111111111", final_text="from-bridge"
            )

    class FakeStore:
        async def append_message(self, *a, **k):
            return 1

        async def append_part(self, *a, **k):
            return None

    class FakeProject:
        root = __import__("pathlib").Path(".")

    rt = AgentRuntime.__new__(AgentRuntime)
    # minimal fields
    rt.bridge = FakeBridge()
    rt.use_bridge_agent_loop = True
    rt.bridge_session_id = None
    rt.mode = "build"
    rt.project = FakeProject()
    rt.session_id = "local-sess"
    rt.store = FakeStore()
    rt.messages = []
    rt.turn_parts = []
    rt.on_event = None
    rt.settings_agent = type("A", (), {"use_desktop_agent_loop": True})()
    rt.settings_llm = None
    rt._lock = __import__("asyncio").Lock()
    rt.cancel_event = __import__("asyncio").Event()

    assert await rt._should_use_bridge_agent_loop("hello") is True
    res = await rt._run_turn_via_bridge("hello")
    assert isinstance(res, TurnResult)
    assert res.ok and res.final_text == "from-bridge"
    assert rt.bridge_session_id.startswith("11111111")
