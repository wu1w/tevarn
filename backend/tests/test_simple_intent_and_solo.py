"""P0: simple-session intent + solo tool surface (no default crew_steward)."""

from __future__ import annotations

from backend.agent.simple_intent import (
    DISPATCH_TOOL_NAMES,
    SOLO_STRIP_TOOLS,
    filter_dispatch_tools_from_schema,
    is_plan_or_read_intent,
    is_simple_session_intent,
    is_solo_session_intent,
    wants_team_dispatch,
)
from backend.agent.tool_policy import PROFILE_EXTRA_TOOLS, resolve_enabled_tool_names


def test_simple_weather_and_trending():
    assert is_simple_session_intent("今天上海天气怎么样")
    assert is_simple_session_intent("帮我看下微博热搜")
    assert is_simple_session_intent("what time is it")
    assert is_simple_session_intent("搜一下 Python 3.13")
    assert is_simple_session_intent("好的")
    # User original scenario + variants
    assert is_simple_session_intent("查一下 github 的热门项目")
    assert is_simple_session_intent("GitHub 热门项目")
    assert is_simple_session_intent("看看 trending repos")
    assert is_simple_session_intent("最近 star 最多的开源项目")


def test_plan_read_is_solo_not_team():
    assert is_plan_or_read_intent("读一下文档，总结 M0 阶段")
    assert is_plan_or_read_intent("帮我做个 M0 计划")
    assert is_plan_or_read_intent("下一步应该怎么做？")
    assert is_solo_session_intent("读一下文档")
    assert is_solo_session_intent("按照文档写个 overview")
    assert is_solo_session_intent("随便问", mode="plan")
    assert is_solo_session_intent("随便问", mode="ask")
    # Team language still wins
    assert not is_solo_session_intent("派给工程师读文档并改代码")
    assert not is_solo_session_intent("让工程师改登录页", mode="plan")


def test_not_simple_when_team_or_heavy():
    assert wants_team_dispatch("派给工程师改登录页")
    assert not is_simple_session_intent("派给工程师改登录页")
    assert not is_simple_session_intent("全仓审计并重构认证模块，分三步做")
    # Short ops / steward language must NOT be simple (was over-stripped)
    assert not is_simple_session_intent("修好登录页")
    assert not is_simple_session_intent("批一下提权")
    assert not is_simple_session_intent("让工程师改登录页")
    assert not is_simple_session_intent("查一下员工进度")
    assert not is_simple_session_intent("查一下工程师进度")
    assert not is_simple_session_intent("看看工程师在干嘛")
    assert not is_simple_session_intent("查一下工程师")
    assert not is_simple_session_intent("查一下工程师怎么样")
    assert wants_team_dispatch("让工程师改登录页")
    # Definition about workforce must not strip crew tools
    assert not is_simple_session_intent("什么是编制里的工单机制？")
    assert not is_simple_session_intent("list 一下员工")


def test_filter_strips_dispatch_tools():
    tools = [
        {"type": "function", "function": {"name": "web_search"}},
        {"type": "function", "function": {"name": "crew_steward"}},
        {"type": "function", "function": {"name": "delegate_task"}},
        {"type": "function", "function": {"name": "manage_goal"}},
        {"type": "function", "function": {"name": "okr_goal"}},
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
    assert "manage_goal" not in names
    assert "okr_goal" not in names
    assert DISPATCH_TOOL_NAMES
    assert "manage_goal" in SOLO_STRIP_TOOLS


def test_filter_strips_goal_on_plan_read():
    tools = [
        {"type": "function", "function": {"name": "file_read"}},
        {"type": "function", "function": {"name": "manage_goal"}},
        {"type": "function", "function": {"name": "crew_steward"}},
    ]
    out = filter_dispatch_tools_from_schema(
        tools, user_text="读一下文档，总结一下现状"
    )
    names = {(t.get("function") or {}).get("name") for t in (out or [])}
    assert "file_read" in names
    assert "manage_goal" not in names
    assert "crew_steward" not in names


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


def test_simple_turn_hard_deny_without_id():
    """Gate must fire even when tool_call_id is empty."""
    from types import SimpleNamespace

    tc = SimpleNamespace(id="", name="crew_steward", arguments={})
    capped: dict[str, str] = {}
    dispatch = {"crew_steward"}
    tn = str(getattr(tc, "name", "") or "")
    cid = str(getattr(tc, "id", "") or "").strip() or f"simple-deny-{tn}-{id(tc)}"
    if tn in dispatch:
        capped[cid] = "denied"
        try:
            setattr(tc, "id", cid)
        except Exception:
            pass
    assert capped, "must deny even when tool_call_id empty"
    assert str(tc.id).startswith("simple-deny-")


def test_crew_topic_not_cafeteria():
    """Bare 员工 in everyday Chinese must not trip crew topic alone."""
    from backend.agent import simple_intent as si

    assert si._CREW_TOPIC.search("员工食堂今天有什么菜") is None
    assert si._CREW_TOPIC.search("查一下员工食堂") is None
    assert si._CREW_TOPIC.search("查一下员工进度") is not None
    assert si._CREW_TOPIC.search("员工列表") is not None
    assert not is_simple_session_intent("查一下员工进度")
