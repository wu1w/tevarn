"""Context policy: thrashing, meter, static archive, rag assist."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from takton_code.context.compressor import ContextCompressor, TokenMeter, validate_tool_integrity
from takton_code.context.policy import (
    ArchiveRetain,
    ThrashingGuard,
    build_context_meter,
    format_context_meter,
    rag_assist_summary,
)


def test_thrashing_trips_and_cools():
    g = ThrashingGuard(max_events=3, window_sec=60, cooldown_sec=0.01)
    assert not g.active
    assert not g.record(kind="middle")
    assert not g.record(kind="middle")
    assert g.record(kind="middle")  # 3rd trips
    assert g.active
    g.reset()
    assert not g.active
    # microcompact kind should not count
    assert not g.record(kind="micro")
    assert not g.active


def test_context_meter_dual():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "x" * 400}]
    m = build_context_meter(
        msgs,
        context_window=1000,
        threshold_percent=0.5,
        usage_totals={"prompt_tokens": 900, "completion_tokens": 50, "total_tokens": 950},
        compress_count=2,
        thrashing={"thrashing": False},
        mode="static",
    )
    assert m["estimate_tokens"] > 0
    assert m["billed_total_tokens"] == 950
    assert "bar" in m
    text = format_context_meter(m)
    assert "ctx" in text and "billed" in text


def test_archive_retain_writes_full(tmp_path: Path):
    ar = ArchiveRetain(root=tmp_path / "archives", session_id="s1", retain_turns=10)
    msgs = [
        {"role": "user", "content": "hello full keep"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "glob", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "glob", "content": "a.py\n" * 50},
    ]
    p = ar.append_messages(msgs, note="test")
    assert p.is_file()
    body = p.read_text(encoding="utf-8")
    assert "hello full keep" in body
    assert "glob" in body
    assert ar.tail_text(200)


def test_static_compress_archives_and_keeps_integrity(tmp_path: Path):
    meter = TokenMeter(context_window=4000, threshold_percent=0.25)
    c = ContextCompressor(
        meter=meter,
        keep_recent=6,
        keep_recent_tool_blocks=2,
        max_tool_chars=300,
        offload_dir=tmp_path / "off",
        compact_mode="static",
        retain_turns=8,
        archive_dir=tmp_path / "archives",
        session_id="sessA",
    )
    blob = "DATA " * 400
    msgs: list[dict] = [{"role": "system", "content": "sys"}]
    for i in range(16):
        msgs.append({"role": "user", "content": f"u{i} " + blob[:80]})
        cid = f"id{i}"
        msgs.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": cid,
                        "type": "function",
                        "function": {"name": "read", "arguments": "{}"},
                    }
                ],
            }
        )
        msgs.append({"role": "tool", "tool_call_id": cid, "name": "read", "content": blob})
        msgs.append({"role": "assistant", "content": f"ok{i}"})
    out = c.compress(msgs, force=True, reason="threshold")
    assert validate_tool_integrity(out) == []
    assert c.last_archive_path
    assert Path(c.last_archive_path).is_file()
    # thrashing blocks middle
    c.block_middle = True
    before = len(out)
    out2 = c.compress(out, force=False, reason="threshold", block_middle=True)
    assert validate_tool_integrity(out2) == []


@pytest.mark.asyncio
async def test_rag_assist_null_bridge():
    class B:
        enabled = False

    s = await rag_assist_summary(B(), query="test")
    assert s == ""


@pytest.mark.asyncio
async def test_rag_assist_fake_hits():
    class Hit:
        def __init__(self):
            self.content = "from knowledge base"
            self.score = 0.9
            self.source = "kb.md"

    class B:
        enabled = True

        async def rag_search(self, req):
            return [Hit()]

    s = await rag_assist_summary(B(), query="auth")
    assert "DESKTOP_RAG_CONTEXT" in s
    assert "knowledge" in s
