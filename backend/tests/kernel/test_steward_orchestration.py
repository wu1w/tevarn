"""管家编排：提示词 + 工具面 + 派活不走子代理闷跑。"""

from __future__ import annotations

from backend.agent.tool_policy import (
    PROFILE_EXTRA_TOOLS,
    TOOL_PACKS,
    compact_capability_brief,
    resolve_enabled_tool_names,
)
from backend.agent.workforce_dispatch import (
    STEWARD_FORCE_TOOLS,
    is_steward_contact,
    steward_orchestration_prompt,
)


def test_is_steward_contact_names():
    assert is_steward_contact("小白")
    assert is_steward_contact("CEO")
    assert is_steward_contact("我的大管家")
    assert is_steward_contact("steward-bot")
    assert not is_steward_contact("kernel-engineer")
    assert not is_steward_contact("")
    assert not is_steward_contact(None)


def test_steward_prompt_mentions_assign_not_subagent_swarm():
    p = steward_orchestration_prompt(contact_name="小白")
    assert "小白" in p
    assert "crew_steward" in p
    assert "assign" in p
    assert "临时" in p or "子代理" in p


def test_crew_pack_and_profile_extras():
    assert "crew_steward" in TOOL_PACKS["crew"]
    assert "crew_steward" in PROFILE_EXTRA_TOOLS["coding"]
    assert "crew_steward" in STEWARD_FORCE_TOOLS


def test_coding_profile_always_has_crew_steward():
    names, plan = resolve_enabled_tool_names(
        mode="default",
        profile="coding",
        user_input="你好",
    )
    assert names is not None
    assert "crew_steward" in names


def test_steward_extra_packs_include_assign_tools():
    names, plan = resolve_enabled_tool_names(
        mode="default",
        profile="coding",
        user_input="请组织工程师巡检",
        extra=list(STEWARD_FORCE_TOOLS),
        extra_packs=["crew"],
    )
    assert names is not None
    for t in ("crew_steward", "delegate_task", "agent_call"):
        assert t in names, t
    assert "crew" in plan.packs or "crew_steward" in names


def test_capability_brief_workforce_line():
    brief = compact_capability_brief(["crew_steward", "file_read"])
    assert "Workforce" in brief or "crew_steward" in brief
