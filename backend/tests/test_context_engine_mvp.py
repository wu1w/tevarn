"""Tests for TokenMeter and context pipeline L1/L3."""

from __future__ import annotations

import asyncio

import pytest

from backend.agent.token_meter import TokenMeter
from backend.agent.context_pipeline import PipelineContextEngine
from backend.agent.file_context import load_memory_md, find_memory_md


def test_rough_estimate_scales_with_content():
    m = TokenMeter(context_window=128_000)
    short = [{"role": "user", "content": "hi"}]
    long = [{"role": "user", "content": "x" * 3400}]
    assert m.estimate_messages(long) > m.estimate_messages(short) * 5


def test_usage_update_and_threshold():
    m = TokenMeter(context_window=10_000, threshold_percent=0.75)
    m.update_from_response(
        {"prompt_tokens": 8000, "completion_tokens": 100, "total_tokens": 8100}
    )
    assert m.should_compress() is True
    assert m.last_prompt_tokens == 8000
    assert m.remaining() == 2000


def test_l1_truncates_tool_output():
    eng = PipelineContextEngine()
    eng.max_tool_output_chars = 100
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "tool", "content": "Z" * 500, "tool_call_id": "1"},
    ]
    out, n = eng._l1_budget(msgs)
    assert n == 1
    assert len(out[2]["content"]) < 400
    assert "L1" in out[2]["content"] or "truncated" in out[2]["content"]


def test_l3_drops_mid_tools():
    eng = PipelineContextEngine()
    eng.protect_first_n = 1
    eng.protect_last_n = 2
    msgs = [{"role": "system", "content": "s"}]
    msgs.append({"role": "user", "content": "start"})
    for i in range(8):
        msgs.append({"role": "assistant", "content": f"a{i}", "tool_calls": [{"id": str(i), "function": {"name": "t", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "content": f"tool{i}" * 20, "tool_call_id": str(i)})
    msgs.append({"role": "user", "content": "latest"})
    msgs.append({"role": "assistant", "content": "end"})
    out, n = eng._l3_microcompact(msgs)
    assert n >= 3
    mid_tools = [m for m in out if m.get("role") == "tool"]
    # tail may keep some tools; overall tool count should drop
    orig_tools = sum(1 for m in msgs if m.get("role") == "tool")
    assert len(mid_tools) < orig_tools


def test_pipeline_compress_l1_only_when_under_threshold():
    eng = PipelineContextEngine()
    eng.enable_l5 = False
    eng.threshold_percent = 0.99
    eng.meter.threshold_percent = 0.99
    eng.max_tool_output_chars = 50
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q"},
        {"role": "tool", "content": "Y" * 200, "tool_call_id": "t1"},
    ]

    async def run():
        return await eng.compress(msgs)

    out, meta = asyncio.run(run())
    assert meta.get("layers")
    assert any(str(x).startswith("L1") for x in meta["layers"])
    # L1 may add a short marker; content body must shrink
    assert len(out[2]["content"]) < len(msgs[2]["content"])
    assert meta.get("layers")


def test_memory_md_loader(tmp_path, monkeypatch):
    mem = tmp_path / "memory.md"
    mem.write_text("# Mem\nhello workspace", encoding="utf-8")
    text, path = load_memory_md(extra_roots=[tmp_path])
    assert "hello workspace" in text
    assert path is not None
