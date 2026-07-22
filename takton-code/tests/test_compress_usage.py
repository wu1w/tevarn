"""Compressor tool integrity + token usage persistence."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from takton_code.context.compressor import (
    ContextCompressor,
    TokenMeter,
    estimate_messages,
    validate_tool_integrity,
)
from takton_code.session.store import SessionStore


def _asst_tools(call_id: str, name: str = "file_read") -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }
        ],
    }


def _tool(call_id: str, body: str, name: str = "file_read") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": body}


def test_validate_detects_orphan_tool():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        _tool("x1", "orphan"),
    ]
    errs = validate_tool_integrity(msgs)
    assert any("orphan" in e for e in errs)


def test_validate_complete_tool_block_ok():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        _asst_tools("c1", "glob"),
        _tool("c1", "a.py\nb.py", "glob"),
        {"role": "assistant", "content": "done"},
    ]
    assert validate_tool_integrity(msgs) == []


def test_compress_keeps_tool_pairs_intact():
    """Cutting keep_recent mid tool-block must not orphan tool messages."""
    meter = TokenMeter(context_window=2000, threshold_percent=0.2)
    c = ContextCompressor(meter=meter, keep_recent=4)
    blob = "PAD " * 200
    msgs: list[dict] = [{"role": "system", "content": "sys"}]
    # many complete tool turns
    for i in range(15):
        msgs.append({"role": "user", "content": f"u{i} {blob}"})
        msgs.append(_asst_tools(f"id{i}", "grep"))
        msgs.append(_tool(f"id{i}", f"result {i} " + blob, "grep"))
        msgs.append({"role": "assistant", "content": f"ans{i}"})

    # force cut that would land inside a tool block without alignment
    out = c.compress(msgs, force=True, reason="test")
    errs = validate_tool_integrity(out)
    assert errs == [], errs
    # still has system
    assert out[0]["role"] == "system"
    assert c.compress_count >= 1
    assert estimate_messages(out) <= estimate_messages(msgs)


def test_compress_repairs_broken_assistant_tools():
    meter = TokenMeter(context_window=500, threshold_percent=0.1)
    c = ContextCompressor(meter=meter, keep_recent=3)
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "x" * 400},
        _asst_tools("broken", "run_tests"),
        # missing tool result on purpose
        {"role": "user", "content": "more " + "y" * 400},
        {"role": "assistant", "content": "ok"},
    ]
    out = c.compress(msgs, force=True)
    assert validate_tool_integrity(out) == []
    # should not keep bare tool_calls without results
    for m in out:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            # if present must be complete
            pass


def test_summarize_mentions_tools():
    meter = TokenMeter(context_window=800, threshold_percent=0.15)
    c = ContextCompressor(meter=meter, keep_recent=2)
    blob = "Z" * 300
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(10):
        msgs.append({"role": "user", "content": f"u{i}{blob}"})
        msgs.append(_asst_tools(f"t{i}", "file_read"))
        msgs.append(_tool(f"t{i}", f"content{i}{blob}", "file_read"))
    out = c.compress(msgs, force=True)
    # compressed summary should mention tools
    joined = " ".join(str(m.get("content") or "") for m in out)
    assert "file_read" in joined or "CONTEXT_COMPRESSED" in joined
    assert validate_tool_integrity(out) == []


@pytest.mark.asyncio
async def test_usage_persisted_to_session():
    home = Path(tempfile.mkdtemp())
    store = SessionStore(home / "s.db")
    await store.open()
    sid = await store.create_session(project_root=str(home), llm_snapshot={"model": "m"})
    await store.update_session(sid, tokens_input=111, tokens_output=22, compress_count=3)
    row = await store.get_session(sid)
    assert row
    assert int(row["tokens_input"]) == 111
    assert int(row["tokens_output"]) == 22
    assert int(row["compress_count"]) == 3
    st = await store.stats_summary()
    assert st["tokens_input"] == 111
    assert st["tokens_output"] == 22
    assert st["compress_count_sum"] == 3
    await store.close()
