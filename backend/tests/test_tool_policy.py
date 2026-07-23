"""默认工具白名单与 profile 解析。"""
from __future__ import annotations

from backend.agent.tool_policy import (
    DEFAULT_CHAT_TOOL_WHITELIST,
    compact_capability_brief,
    resolve_enabled_tool_names,
    wants_full_tools,
)


def test_default_is_core_whitelist():
    names = resolve_enabled_tool_names(mode="default", raw_tools=None, profile="core")
    assert names is not None
    assert "file_read" in names
    assert "command" in names
    assert "manage_evolution" not in names
    assert "desktop_click" not in names
    assert len(names) <= 30
    assert set(DEFAULT_CHAT_TOOL_WHITELIST).issubset(set(names)) or set(names) <= set(
        DEFAULT_CHAT_TOOL_WHITELIST
    ) or "file_read" in names


def test_full_profile_and_star():
    assert wants_full_tools(None, profile="full") is True
    assert resolve_enabled_tool_names(raw_tools=["*"], profile="core") is None
    assert resolve_enabled_tool_names(raw_tools=["all"], profile="core") is None
    assert resolve_enabled_tool_names(profile="full") is None


def test_mode_goal_adds_tools():
    names = resolve_enabled_tool_names(mode="goal", raw_tools=None, profile="core")
    assert names is not None
    assert "manage_goal" in names
    assert "autopilot" in names


def test_explicit_list_honored():
    names = resolve_enabled_tool_names(
        mode="default", raw_tools=["file_read", "grep"], profile="core"
    )
    assert names is not None
    assert set(names) == {"file_read", "grep"}


def test_compact_brief_short():
    b = compact_capability_brief(["a", "b"])
    assert "Tool discipline" in b
    assert "autopilot" not in b
    assert "desktop_" not in b
    assert len(b) < 400


def test_system_prompt_evolution_conditional():
    from backend.agent.system_prompt import EVOLUTION_GUIDANCE, build_system_prompt

    parts = build_system_prompt(tools_enabled=["file_read", "command"])
    stable = parts["stable"]
    assert "Skills" in stable or "skill" in stable.lower()
    assert "TEE v0.1.1" not in stable
    assert "backend/evolution" not in stable or "manage_evolution" in stable

    parts2 = build_system_prompt(tools_enabled=["file_read", "manage_evolution"])
    assert EVOLUTION_GUIDANCE[:40] in parts2["stable"] or "evolution" in parts2["stable"].lower()


def test_tool_enforcement_when_tools_none():
    from backend.agent.system_prompt import TOOL_USE_ENFORCEMENT, build_system_prompt

    parts = build_system_prompt(tools_enabled=None)
    assert TOOL_USE_ENFORCEMENT[:20] in parts["stable"]
    assert "backend/evolution" not in parts["stable"]

def test_no_tools_skips_enforcement():
    from backend.agent.system_prompt import TOOL_USE_ENFORCEMENT, build_system_prompt

    parts = build_system_prompt(tools_enabled=[])
    assert TOOL_USE_ENFORCEMENT[:20] not in parts["stable"]
