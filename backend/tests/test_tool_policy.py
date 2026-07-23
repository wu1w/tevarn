"""工具策略：core / dynamic 场景 / full / 注入档位。"""
from __future__ import annotations

from backend.agent.tool_policy import (
    DEFAULT_CHAT_TOOL_WHITELIST,
    compact_capability_brief,
    infer_scene,
    injection_knobs,
    merge_tools_with_packs,
    resolve_enabled_tool_names,
    wants_full_tools,
)


def test_default_dynamic_coding_scene():
    names, plan = resolve_enabled_tool_names(
        mode="default",
        profile="dynamic",
        user_input="帮我修这个 Python bug，traceback 在下面",
    )
    assert names is not None
    assert "file_read" in names
    assert "use_tool_pack" in names
    assert "coding" in plan.packs
    assert "manage_evolution" not in names
    assert "desktop_click" not in names


def test_dynamic_desktop_and_manage():
    names, plan = resolve_enabled_tool_names(
        mode="default",
        profile="dynamic",
        user_input="帮我在桌面点击那个按钮，并配置一下 cron 定时任务",
    )
    assert names is not None
    assert "desktop" in plan.packs
    assert "manage" in plan.packs
    assert "desktop_click" in names
    assert "manage_cron" in names


def test_core_profile_no_kw_expand():
    names, plan = resolve_enabled_tool_names(
        mode="default",
        profile="core",
        user_input="配置 cron 和桌面点击",
    )
    assert names is not None
    assert "manage_cron" not in names
    assert "desktop_click" not in names
    assert "use_tool_pack" in names


def test_full_profile_and_star():
    assert wants_full_tools(None, profile="full") is True
    names, plan = resolve_enabled_tool_names(raw_tools=["*"], profile="core")
    assert names is None
    names2, _ = resolve_enabled_tool_names(profile="full")
    assert names2 is None


def test_mode_goal_adds_tools():
    names, plan = resolve_enabled_tool_names(mode="goal", profile="dynamic", user_input="x")
    assert names is not None
    assert "manage_goal" in names
    assert "autopilot" in names


def test_explicit_list_honored():
    names, _ = resolve_enabled_tool_names(
        mode="default", raw_tools=["file_read", "grep"], profile="core"
    )
    assert names is not None
    assert "file_read" in names and "grep" in names
    assert "use_tool_pack" in names  # meta always
    assert "command" not in names


def test_merge_packs_midloop():
    base = list(DEFAULT_CHAT_TOOL_WHITELIST)
    merged = merge_tools_with_packs(base, ["desktop"])
    assert merged is not None
    assert "desktop_click" in merged
    assert merge_tools_with_packs(base, ["full"]) is None


def test_injection_tiers():
    assert injection_knobs("minimal")["rag"] is False
    assert injection_knobs("standard")["rag"] is True
    assert injection_knobs("rich")["rag_top_k"] == 5


def test_greeting_minimal_tier():
    plan = infer_scene("你好", mode="default", profile="dynamic")
    assert plan.injection_tier == "minimal"


def test_knowledge_rich_tier():
    plan = infer_scene("知识库里 Takton 是什么架构？", profile="dynamic")
    assert plan.injection_tier == "rich"


def test_compact_brief_mentions_pack():
    plan = infer_scene("x", profile="dynamic")
    b = compact_capability_brief(["a", "b"], scene=plan)
    assert "use_tool_pack" in b
    assert "autopilot" not in b or "Scene" in b
    assert len(b) < 600


def test_system_prompt_evolution_conditional():
    from backend.agent.system_prompt import EVOLUTION_GUIDANCE, build_system_prompt

    parts = build_system_prompt(tools_enabled=["file_read", "command"])
    stable = parts["stable"]
    assert "backend/evolution" not in stable

    parts2 = build_system_prompt(tools_enabled=["file_read", "manage_evolution"])
    assert "evolution" in parts2["stable"].lower()


def test_tool_enforcement_when_tools_none():
    from backend.agent.system_prompt import TOOL_USE_ENFORCEMENT, build_system_prompt

    parts = build_system_prompt(tools_enabled=None)
    assert TOOL_USE_ENFORCEMENT[:20] in parts["stable"]
    assert "backend/evolution" not in parts["stable"]


def test_no_tools_skips_enforcement():
    from backend.agent.system_prompt import TOOL_USE_ENFORCEMENT, build_system_prompt

    parts = build_system_prompt(tools_enabled=[])
    assert TOOL_USE_ENFORCEMENT[:20] not in parts["stable"]


def test_profile_coding_no_manage():
    names, plan = resolve_enabled_tool_names(
        mode="default", profile="coding", user_input="配置 cron 和桌面点击"
    )
    assert names is not None
    assert "manage_cron" not in names
    assert "desktop_click" not in names
    assert "file_read" in names
    assert "use_tool_pack" in names
    assert plan.profile == "coding"


def test_profile_ops_has_manage():
    names, plan = resolve_enabled_tool_names(
        mode="default", profile="ops", user_input="hello"
    )
    assert names is not None
    assert "manage_cron" in names
    assert "file_read" in names


def test_profile_assistant_has_session_search():
    names, _ = resolve_enabled_tool_names(profile="assistant", user_input="x")
    assert names is not None
    assert "session_search" in names
