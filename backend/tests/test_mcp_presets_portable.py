"""MCP presets must resolve to a real executable on any machine."""

from pathlib import Path

from backend.core.host_commands import resolve_existing_command
from backend.services.mcp_presets import (
    McpRunnerUnavailable,
    find_preset,
    resolve_preset_command,
)


def test_tavily_uses_builtin_python_not_bare_npx():
    preset = find_preset("tavily")
    assert preset is not None
    command, args, note = resolve_preset_command(preset)
    assert Path(command).is_file(), command
    assert command.lower() not in {"npx", "npx.cmd"}
    joined = " ".join(args)
    assert "runners/tavily.py" in joined or args[:2] == ["-m", "backend.mcp_hub.runners.tavily"]
    assert "tavily" in note


def test_resolve_existing_command_none_for_missing_binary():
    assert resolve_existing_command("definitely-not-a-host-bin-xyz") is None


def test_npx_only_preset_raises_when_npx_missing(monkeypatch):
    from backend.services import mcp_presets as mp
    from backend.services.mcp_presets import McpPreset

    monkeypatch.setattr(mp, "resolve_npx_path", lambda: None)
    monkeypatch.setattr(mp, "resolve_uvx_path", lambda: None)
    preset = McpPreset(
        id="only-npx",
        display_name="only-npx",
        aliases=(),
        env_key="",
        runners=(("npx_pkg", "whatever", ("-y", "whatever")),),
    )
    try:
        resolve_preset_command(preset)
        raise AssertionError("should have raised")
    except McpRunnerUnavailable as e:
        assert "npx" in str(e)


def test_resolve_does_not_follow_venv_symlink(tmp_path, monkeypatch):
    import os
    from backend.core import host_commands as hc

    real = tmp_path / "real-python"
    real.write_text("#!/bin/sh\n")
    real.chmod(0o755)
    link = tmp_path / "venv-python"
    link.symlink_to(real)
    got = hc.resolve_host_command(str(link))
    assert os.path.basename(got) == "venv-python"
