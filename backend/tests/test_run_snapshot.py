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
