"""P0: simple-session intent + solo tool surface (no default crew_steward)."""

from __future__ import annotations

from backend.agent.simple_intent import (
    DISPATCH_TOOL_NAMES,
    filter_dispatch_tools_from_schema,
    is_simple_session_intent,
    wants_team_dispatch,
)
from backend.agent.tool_policy import PROFILE_EXTRA_TOOLS, resolve_enabled_tool_names


def test_simple_weather_and_trending():
    assert is_simple_session_intent("今天上海天气怎么样")
    assert is_simple_session_intent("帮我看下微博热搜")
    assert is_simple_session_intent("what time is it")
    assert is_simple_session_intent("搜一下 Python 3.13")
    assert is_simple_session_intent("好的")


def test_not_simple_when_team_or_heavy():
    assert wants_team_dispatch("派给工程师改登录页")
    assert not is_simple_session_intent("派给工程师改登录页")
    assert not is_simple_session_intent("全仓审计并重构认证模块，分三步做")
    # Short ops / steward language must NOT be simple (was over-stripped)
    assert not is_simple_session_intent("修好登录页")
    assert not is_simple_session_intent("批一下提权")
    assert not is_simple_session_intent("让工程师改登录页")
    assert not is_simple_session_intent("查一下员工进度")
    assert wants_team_dispatch("让工程师改登录页")


def test_filter_strips_dispatch_tools():
    tools = [
        {"type": "function", "function": {"name": "web_search"}},
        {"type": "function", "function": {"name": "crew_steward"}},
        {"type": "function", "function": {"name": "delegate_task"}},
        {"type": "function", "function": {"name": "current_time"}},
    ]
    out = filter_dispatch_tools_from_schema(tools, user_text="今天天气")
    names = {
        (t.get("function") or {}).get("name")
        for t in (out or [])
    }
    assert "web_search" in names
    assert "current_time" in names
    assert "crew_steward" not in names
    assert "delegate_task" not in names
    assert DISPATCH_TOOL_NAMES


def test_filter_keeps_dispatch_when_team_ask():
    tools = [
        {"type": "function", "function": {"name": "crew_steward"}},
        {"type": "function", "function": {"name": "web_search"}},
    ]
    out = filter_dispatch_tools_from_schema(
        tools, user_text="派给研究员查一下竞品"
    )
    names = {(t.get("function") or {}).get("name") for t in (out or [])}
    assert "crew_steward" in names


def test_profile_extra_tools_no_default_crew():
    for prof, extras in PROFILE_EXTRA_TOOLS.items():
        assert "crew_steward" not in extras, prof


def test_coding_resolve_excludes_crew_unless_steward_pack():
    names, _plan = resolve_enabled_tool_names(
        mode="default",
        profile="coding",
        user_input="今天天气",
        extra_packs=None,
    )
    assert names is not None
    assert "crew_steward" not in names
    assert "current_time" in names or "use_tool_pack" in names

    names2, _ = resolve_enabled_tool_names(
        mode="default",
        profile="coding",
        user_input="复杂项目",
        extra_packs=["crew"],
    )
    assert names2 is not None
    assert "crew_steward" in names2


def test_cluster_mode_includes_crew():
    names, _ = resolve_enabled_tool_names(
        mode="cluster",
        profile="coding",
        user_input="并行调研",
    )
    assert names is not None
    assert "crew_steward" in names or "manage_sub_agent" in names
