"""Decisive batching heuristics."""
from __future__ import annotations

from backend.agent.decisive import (
    batch_read_nudge_text,
    batch_write_nudge_text,
    decisive_coding_guidance,
    is_timid_read_round,
    is_timid_write_round,
    tool_names_from_calls,
)
from backend.agent.system_prompt import PARALLEL_TOOL_CALLS, build_system_prompt


def test_timid_single_file_read():
    assert is_timid_read_round(["file_read"]) is True
    assert is_timid_read_round(["grep"]) is True
    assert is_timid_read_round(["file_read", "grep"]) is False
    assert is_timid_read_round(["edit"]) is False
    assert is_timid_read_round([]) is False


def test_tool_names_from_calls_objects():
    class T:
        def __init__(self, name):
            self.name = name

    assert tool_names_from_calls([T("file_read"), T("grep")]) == ["file_read", "grep"]


def test_nudge_and_guidance_text():
    n = batch_read_nudge_text(consecutive_timid=2)
    assert "并行" in n or "batch" in n.lower() or "多个" in n
    g = decisive_coding_guidance()
    assert "file_read" in g
    assert "HARD RULE" in PARALLEL_TOOL_CALLS or "SAME turn" in PARALLEL_TOOL_CALLS


def test_system_prompt_includes_decisive_for_code_tools():
    parts = build_system_prompt(tools_enabled=["file_read", "command", "edit"])
    stable = parts["stable"]
    assert "Decisive batching" in stable or "batch" in stable.lower()
    assert "SAME turn" in PARALLEL_TOOL_CALLS


def test_timid_write_and_nudge():
    assert is_timid_write_round(["file_write"]) is True
    assert is_timid_write_round(["file_write", "file_write"]) is False
    n = batch_write_nudge_text(consecutive_timid=2)
    assert "file_write" in n
