"""Session run snapshot: navigate-away safe state for sync_response."""

from __future__ import annotations

import uuid

import pytest

from backend.api.websocket import ConnectionManager, SessionRunSnapshot


def test_ingest_stream_without_connection():
    """无 WS 连接时仍应累积 partial（用户在别的页面）。"""
    m = ConnectionManager()
    sid = uuid.uuid4()
    m.begin_run_snapshot(sid)
    m._ingest_run_event(
        sid,
        {"type": "stream_delta", "message_id": "m1", "content": "Hello "},
    )
    m._ingest_run_event(
        sid,
        {"type": "stream_delta", "message_id": "m1", "content": "world"},
    )
    snap = m.get_run_snapshot(sid)
    assert snap is not None
    assert snap.partial_content == "Hello world"
    assert snap.agent_running is True


def test_stream_delta_buf_flush_on_read_and_manager_alias():
    """合帧缓冲：未达 24 帧阈值时读快照必须 flush；_flush_delta_buf 别名可用。"""
    m = ConnectionManager()
    sid = uuid.uuid4()
    m.begin_run_snapshot(sid)
    # 两帧远低于 24 / 800 阈值
    m._ingest_run_event(
        sid, {"type": "stream_delta", "message_id": "x", "content": "ab"}
    )
    m._ingest_run_event(
        sid, {"type": "stream_delta", "message_id": "x", "content": "cd"}
    )
    raw = m._run_snapshots.get(sid)
    assert raw is not None
    # 未读前可能仍在 _delta_buf
    buf = getattr(raw, "_delta_buf", None)
    # 经 manager 别名刷
    m._flush_delta_buf(raw)
    assert raw.partial_content == "abcd"
    assert not buf  # cleared
    # to_sync_fields 也要带上
    f = raw.to_sync_fields()
    assert f["partial_content"] == "abcd"


def test_ingest_tool_events_upsert():
    m = ConnectionManager()
    sid = uuid.uuid4()
    m.begin_run_snapshot(sid)
    m._ingest_run_event(
        sid,
        {
            "type": "tool_event",
            "phase": "start",
            "tool_call_id": "c1",
            "name": "file_read",
            "arguments": {"path": "a.py"},
            "status": "running",
        },
    )
    m._ingest_run_event(
        sid,
        {
            "type": "tool_event",
            "phase": "end",
            "tool_call_id": "c1",
            "name": "file_read",
            "status": "completed",
            "result": "ok",
        },
    )
    snap = m.get_run_snapshot(sid)
    assert snap is not None
    assert len(snap.live_tools) == 1
    assert snap.live_tools[0]["id"] == "c1"
    assert snap.live_tools[0]["status"] == "completed"
    assert snap.live_tools[0]["result"] == "ok"


def test_to_sync_fields_when_running():
    snap = SessionRunSnapshot(
        agent_running=True,
        state="tool_executing",
        detail="reading",
        partial_content="partial",
        live_tools=[{"id": "1", "name": "t", "status": "running"}],
    )
    f = snap.to_sync_fields()
    assert f["agent_running"] is True
    assert f["partial_content"] == "partial"
    assert f["live_tools"][0]["name"] == "t"


def test_idle_clears_when_no_agent_task():
    m = ConnectionManager()
    sid = uuid.uuid4()
    m.begin_run_snapshot(sid)
    m._ingest_run_event(sid, {"type": "stream_delta", "content": "x", "message_id": "1"})
    # no agent task registered → idle should clear
    m._ingest_run_event(sid, {"type": "status", "state": "idle", "detail": "done"})
    assert m.get_run_snapshot(sid) is None


@pytest.mark.asyncio
async def test_broadcast_ingests_even_without_ws():
    m = ConnectionManager()
    sid = uuid.uuid4()
    m.begin_run_snapshot(sid)
    await m.broadcast(
        sid,
        {"type": "stream_delta", "message_id": "a", "content": "bg"},
    )
    snap = m.get_run_snapshot(sid)
    assert snap is not None
    assert "bg" in snap.partial_content


def test_idle_tombstone_beats_unwinding_task():
    """Idle from the loop must not stay 'running' just because the task is unwinding."""
    import asyncio

    m = ConnectionManager()
    sid = uuid.uuid4()
    m.begin_run_snapshot(sid)

    async def _linger():
        await asyncio.sleep(60)

    task = asyncio.get_event_loop().create_task(_linger()) if False else None
    # Register a not-done dummy by using a Future-like: track via _agent_tasks
    class _Fake:
        def done(self):
            return False
        def add_done_callback(self, cb):
            self._cb = cb
        def cancel(self):
            pass
    fake = _Fake()
    m._agent_tasks[sid] = fake  # type: ignore[assignment]
    assert m.has_running_agent(sid) is True
    m._ingest_run_event(sid, {"type": "status", "state": "idle", "detail": "Ready"})
    snap = m.get_run_snapshot(sid)
    assert snap is not None
    assert snap.state == "idle"
    assert snap.agent_running is False
    # sync-style: idle wins
    running = m.has_running_agent(sid)
    if snap is not None and snap.state in ("idle", "error"):
        running = False
    assert running is False
    m._agent_tasks.pop(sid, None)


def test_live_thinking_snap_not_hidden_by_idle_label():
    """Live agent + agent_running snap stays running even if state string is idle."""
    m = ConnectionManager()
    sid = uuid.uuid4()
    m.begin_run_snapshot(sid)

    class _Fake:
        def done(self):
            return False

        def add_done_callback(self, cb):
            pass

        def cancel(self):
            pass

    m._agent_tasks[sid] = _Fake()  # type: ignore[assignment]
    snap = m.get_run_snapshot(sid)
    assert snap is not None
    snap.state = "idle"
    snap.agent_running = True
    running = m.has_running_agent(sid)
    if (
        snap is not None
        and str(getattr(snap, "state", "") or "") in ("idle", "error")
        and not bool(getattr(snap, "agent_running", False))
    ):
        running = False
    assert running is True
    m._agent_tasks.pop(sid, None)
