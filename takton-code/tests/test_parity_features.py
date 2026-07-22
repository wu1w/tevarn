"""Parity features: stream parse, @refs, permission cycle, subagent wall."""

from __future__ import annotations

from pathlib import Path

import pytest

from takton_code.agent.refs import (
    SLASH_COMMANDS,
    cycle_permission_mode,
    expand_at_refs,
    filter_slash_commands,
)
from takton_code.llm.provider import (
    StreamDelta,
    _ToolCallAccumulator,
    parse_sse_data_lines,
)


def test_slash_has_check():
    assert any(c == "/check" for c, _ in SLASH_COMMANDS)


def test_permission_cycle():
    assert cycle_permission_mode("build") == "plan"
    assert cycle_permission_mode("plan") == "always"
    assert cycle_permission_mode("always") == "build"
    # unknown falls through to build → next is plan
    assert cycle_permission_mode("explore") == "plan"
    assert cycle_permission_mode("ask") == "plan"


def test_slash_filter():
    hits = filter_slash_commands("/pl")
    assert any(c == "/plan" for c, _ in hits)
    assert filter_slash_commands("/usage")


def test_expand_at_refs(tmp_path: Path):
    f = tmp_path / "src" / "a.py"
    f.parent.mkdir(parents=True)
    f.write_text("print(1)\n", encoding="utf-8")
    out = expand_at_refs("look at @src/a.py please", tmp_path)
    assert "print(1)" in out
    assert "```" in out
    # missing file keeps marker
    out2 = expand_at_refs("@nope.py", tmp_path)
    assert "missing" in out2


def test_tool_call_accumulator():
    acc = _ToolCallAccumulator()
    acc.ingest(
        [
            {"index": 0, "id": "c1", "function": {"name": "grep", "arguments": ""}},
        ]
    )
    acc.ingest([{"index": 0, "function": {"arguments": '{"p'}}])
    acc.ingest([{"index": 0, "function": {"arguments": 'at":"x"}'}}])
    snap = acc.snapshot()
    assert snap[0]["function"]["name"] == "grep"
    assert "pat" in snap[0]["function"]["arguments"]


def test_parse_sse():
    assert parse_sse_data_lines("data: {\"a\":1}\n\ndata: [DONE]\n") == ['{"a":1}', "[DONE]"]


@pytest.mark.asyncio
async def test_readonly_blocks_write(tmp_path: Path):
    from takton_code.agent.tools import ToolRuntime
    from takton_code.diff.engine import DiffEngine

    d = DiffEngine(tmp_path)
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    rt = ToolRuntime(tmp_path, d, mode="plan", enable_subagent=False)
    out = await rt.execute("file_write", {"path": "a.txt", "content": "y"})
    assert out.startswith("ERROR")
    rt.set_mode("always")
    # always uses build tools when parent maps — ToolRuntime always is writeable
    rt.set_mode("build")
    out2 = await rt.execute("edit_file", {"path": "a.txt", "old_string": "x", "new_string": "y"})
    assert out2.startswith("OK")


@pytest.mark.asyncio
async def test_spawn_forbidden_without_runner(tmp_path: Path):
    from takton_code.agent.tools import ToolRuntime
    from takton_code.diff.engine import DiffEngine

    rt = ToolRuntime(tmp_path, DiffEngine(tmp_path), mode="build", enable_subagent=True)
    # registered but runner None
    out = await rt.execute("spawn_subagent", {"prompt": "x", "agent": "explore"})
    assert "ERROR" in out


def test_stream_delta_dataclass():
    d = StreamDelta(content="hi")
    assert d.content == "hi"
