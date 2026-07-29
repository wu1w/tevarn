"""编制权限：员工工具不弹主人。"""

from __future__ import annotations

import pytest

from backend.agent.steward_permission import (
    is_human_strategy_surface,
    is_workforce_context,
    steward_decide_tool,
)


def test_workforce_context_detection():
    assert is_workforce_context({"_workforce": True})
    assert is_workforce_context({"_agent_key": "wf:abc"})
    assert is_workforce_context({"_chat_mode": "workforce"})
    assert not is_workforce_context({"_agent_key": "main"})
    assert not is_workforce_context({})


def test_strategy_surfaces():
    assert is_human_strategy_surface("clarify")
    assert is_human_strategy_surface("manage_goal")
    assert not is_human_strategy_surface("glob")
    assert not is_human_strategy_surface("command")


@pytest.mark.asyncio
async def test_steward_allows_file_tools_with_file_rw():
    d, why = await steward_decide_tool(
        "glob", identity_capabilities=["file_rw", "web_search"]
    )
    assert d == "allow"
    assert "within" in why or "steward" in why

    d2, _ = await steward_decide_tool(
        "grep", identity_capabilities=["file_rw"]
    )
    assert d2 == "allow"


@pytest.mark.asyncio
async def test_steward_denies_command_without_command_cap():
    d, why = await steward_decide_tool(
        "command", identity_capabilities=["file_rw", "web_search"]
    )
    assert d == "deny"
    assert "outside" in why or "no_cap" in why or "caps" in why


@pytest.mark.asyncio
async def test_steward_allows_command_with_command_cap():
    d, _ = await steward_decide_tool(
        "command", identity_capabilities=["file_rw", "command"]
    )
    assert d == "allow"
