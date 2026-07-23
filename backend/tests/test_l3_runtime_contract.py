"""L3: workspace contract + tool hooks + file checkpoint."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.agent.file_checkpoint import snapshot_path_for_tool
from backend.agent.tool_hooks import (
    BeforeHookResult,
    clear_tool_hooks,
    register_after_tool_call,
    register_before_tool_call,
    run_after_tool_call,
    run_before_tool_call,
)
from backend.agent.workspace_contract import load_workspace_contract


def test_workspace_contract_missing_markers(tmp_path, monkeypatch):
    # only empty root → missing markers
    block, meta = load_workspace_contract(extra_roots=[tmp_path], only_extra_roots=True)
    assert "WORKSPACE CONTRACT" in block
    assert "[missing: AGENTS.md" in block
    assert "[missing: SOUL.md" in block
    assert meta["files"]["AGENTS.md"]["status"] == "missing"


def test_workspace_contract_loads_present(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Agents\nUse tools.\n", encoding="utf-8")
    (tmp_path / "USER.md").write_text("User prefers Chinese.\n", encoding="utf-8")
    block, meta = load_workspace_contract(extra_roots=[tmp_path], only_extra_roots=True)
    assert "AGENTS.md" in block and "Use tools" in block
    assert "USER.md" in block
    assert meta["files"]["AGENTS.md"]["status"] == "ok"
    assert meta["files"]["SOUL.md"]["status"] == "missing"


@pytest.mark.asyncio
async def test_before_hook_can_block():
    clear_tool_hooks()

    async def blocker(name, args):
        if name == "file_write":
            return BeforeHookResult(block=True, reason="no writes")
        return BeforeHookResult(arguments=args)

    register_before_tool_call(blocker)
    res = await run_before_tool_call("file_write", {"filepath": "a"})
    assert res.block is True
    res2 = await run_before_tool_call("file_read", {"filepath": "a"})
    assert res2.block is False
    clear_tool_hooks()


@pytest.mark.asyncio
async def test_after_hook_transforms():
    clear_tool_hooks()

    def trimmer(name, args, result):
        return result[:5] + "…"

    register_after_tool_call(trimmer)
    out = await run_after_tool_call("x", {}, "abcdefghij")
    assert out == "abcde…"
    clear_tool_hooks()


def test_file_checkpoint_copies(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # fake project root via env
    monkeypatch.setenv("TAKTON_FILE_BROWSER_ROOT", str(tmp_path))
    f = tmp_path / "sample.txt"
    f.write_text("hello-checkpoint", encoding="utf-8")
    snap = snapshot_path_for_tool("file_write", {"filepath": "sample.txt"})
    assert snap is not None
    assert Path(snap).is_file()
    assert Path(snap).read_text(encoding="utf-8") == "hello-checkpoint"
