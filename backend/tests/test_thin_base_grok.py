"""Grok-style empty base + pack accumulate."""

def test_empty_packs_meta_only():
    from backend.agent.tool_policy import tools_for_packs, ALWAYS_META_TOOLS
    names = set(tools_for_packs([]))
    assert names <= set(ALWAYS_META_TOOLS)
    assert "command" not in names
    assert "browser" not in names


def test_coding_pack_no_web():
    from backend.agent.tool_policy import tools_for_packs
    names = set(tools_for_packs(["coding"]))
    assert "command" in names
    assert "browser" not in names
    assert "web_search" not in names


def test_resolve_thin_and_coding():
    from backend.agent.tool_policy import resolve_enabled_tool_names
    thin, _ = resolve_enabled_tool_names(profile="dynamic", user_input="你好", mode="default")
    assert thin is not None and "command" not in thin
    coding, plan = resolve_enabled_tool_names(
        profile="dynamic", user_input="修这个 TypeError traceback", mode="default"
    )
    assert coding is not None
    assert "browser" not in coding
    assert "web_search" not in coding
    assert "coding" in plan.packs


def test_profile_coding_no_web_pack():
    from backend.agent.tool_policy import PROFILE_BASE_PACKS, resolve_enabled_tool_names
    assert "web" not in PROFILE_BASE_PACKS["coding"]
    names, plan = resolve_enabled_tool_names(
        profile="coding", user_input="实现一个函数", mode="default"
    )
    assert "web" not in plan.packs
    assert names is not None and "browser" not in names


def test_goal_casual():
    from backend.agent.goal_facade import looks_like_casual_or_read_only
    assert looks_like_casual_or_read_only("你好")
    assert looks_like_casual_or_read_only("搜一下新闻")
