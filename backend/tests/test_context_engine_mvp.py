"""Tests for TokenMeter and context pipeline L1/L3."""

from __future__ import annotations

import asyncio

from backend.agent.context_pipeline import (
    PipelineContextEngine,
    collapse_prior_turn_tool_traces,
)
from backend.agent.file_context import load_memory_md
from backend.agent.token_meter import TokenMeter


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


def test_l3_clears_mid_tool_bodies_keeps_pairs():
    """L3 microcompact (CC-style): clear mid tool content, keep tool_calls + tool rows."""
    from backend.agent.context_pipeline import CLEARED_TOOL_PLACEHOLDER

    eng = PipelineContextEngine()
    eng.protect_first_n = 1
    eng.protect_last_n = 2
    msgs = [{"role": "system", "content": "s"}]
    msgs.append({"role": "user", "content": "start"})
    for i in range(8):
        msgs.append(
            {
                "role": "assistant",
                "content": f"a{i}",
                "tool_calls": [
                    {
                        "id": str(i),
                        "function": {"name": "file_read", "arguments": "{}"},
                    }
                ],
            }
        )
        msgs.append(
            {
                "role": "tool",
                "content": f"tool{i}_" + ("X" * 200),
                "tool_call_id": str(i),
            }
        )
    msgs.append({"role": "user", "content": "latest"})
    msgs.append({"role": "assistant", "content": "end"})
    out, n = eng._l3_microcompact(msgs)
    assert n >= 3
    mid_tools = [m for m in out if m.get("role") == "tool"]
    orig_tools = sum(1 for m in msgs if m.get("role") == "tool")
    assert len(mid_tools) == orig_tools
    asst_with_tc = [
        m for m in out if m.get("role") == "assistant" and m.get("tool_calls")
    ]
    assert len(asst_with_tc) >= 3
    cleared = [
        m for m in out if m.get("role") == "tool" and CLEARED_TOOL_PLACEHOLDER in str(m.get("content") or "")
    ]
    assert len(cleared) >= 3


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



def _pair(i: int, name: str, result: str) -> list[dict]:
    cid = f"call_{name}_{i}"
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": cid,
                    "type": "function",
                    "function": {"name": name, "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": cid, "content": result},
    ]


def test_collapse_prior_turn_tool_traces_three_user_turns():
    """3 user turns with many old tool pairs: old turns summarize, latest stays raw."""
    msgs: list[dict] = [{"role": "system", "content": "sys"}]
    msgs.append({"role": "user", "content": "基础检测"})
    for i in range(6):
        msgs.extend(_pair(i, "configure_tevarn", "saved config"))
    for i in range(6):
        msgs.extend(_pair(i, "pytest", "3 failed"))
    msgs.append({"role": "assistant", "content": "检测完成"})
    msgs.append({"role": "user", "content": "复测"})
    for i in range(8):
        msgs.extend(_pair(i, "file_write", "written /tmp/x"))
    msgs.append({"role": "assistant", "content": "复测结束"})
    msgs.append({"role": "user", "content": "再复测"})
    msgs.extend(_pair(0, "file_read", "ok file"))
    msgs.append({"role": "assistant", "content": "working"})

    out, removed = collapse_prior_turn_tool_traces(msgs)
    assert removed > 0
    # latest turn tools kept
    latest_tools = [
        m
        for m in out
        if m.get("role") == "tool" and m.get("tool_call_id") == "call_file_read_0"
    ]
    assert len(latest_tools) == 1
    # no raw prior-turn tool rows
    prior_raw = [
        m
        for m in out
        if m.get("role") == "tool" and m.get("tool_call_id") != "call_file_read_0"
    ]
    assert prior_raw == []
    blob = "\n".join(str(m.get("content") or "") for m in out)
    assert "[prior turn tools]" in blob
    assert "configure_tevarn" in blob
    assert "pytest" in blob
    assert "file_write" in blob
    # user turns + latest assistant with tool_calls survive
    users = [m.get("content") for m in out if m.get("role") == "user"]
    assert users == ["基础检测", "复测", "再复测"]
    latest_asst = [
        m for m in out if m.get("role") == "assistant" and m.get("tool_calls")
    ]
    assert len(latest_asst) == 1
    assert latest_asst[0]["tool_calls"][0]["id"] == "call_file_read_0"

    # L1 path also collapses
    eng = PipelineContextEngine()
    l1_out, n = eng._l1_budget([dict(m) for m in msgs])
    assert n >= removed
    assert not any(
        m.get("role") == "tool" and m.get("tool_call_id", "").startswith("call_pytest")
        for m in l1_out
    )
