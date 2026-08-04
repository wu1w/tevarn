# -*- coding: utf-8 -*-
"""PR1–PR4 loop_guard: role caps, worker ban crew, truncate re-read, budget ratio."""
from __future__ import annotations

import backend.agent.loop_guard_bridge as lgb


def setup_function():
    lgb.reset_local_for_tests()


def test_classify_research_role():
    assert (
        lgb.classify_role_kind(
            workforce=True,
            identity_name="研究员",
            instruction="随便",
        )
        == "research"
    )
    assert (
        lgb.classify_role_kind(
            workforce=True,
            identity_name="工程师",
            instruction="调研 GitHub MCP 成熟做法并只读验证",
        )
        == "research"
    )
    assert (
        lgb.classify_role_kind(
            workforce=True,
            identity_name="工程师",
            instruction="实现登录 API 并写测试",
        )
        == "implement"
    )


def test_build_config_research_bans_orch():
    cfg = lgb.build_loop_guard_config(
        workforce=True,
        identity_name="研究员",
        instruction="调研官方文档",
        payload={"thoroughness": "quick"},
    )
    assert cfg["ban_worker_orch"] is True
    assert cfg["max_crew_total"] == 0
    assert cfg["max_orch_per_round"] == 0
    assert cfg["max_tool_rounds"] == 6  # quick


def test_build_config_implement_max_20():
    cfg = lgb.build_loop_guard_config(
        workforce=True,
        identity_name="工程师",
        instruction="改 backend/loop.py 并跑测试",
    )
    assert cfg["role_kind"] == "implement"
    assert cfg["ban_worker_orch"] is True
    assert cfg["max_tool_rounds"] == 20


def test_local_max_rounds_force_final():
    pid = "test-pid-rounds"
    lgb.configure_for_process(
        pid,
        {
            "workforce": True,
            "role_kind": "research",
            "max_tool_rounds": 2,
            "ban_worker_orch": True,
            "max_crew_total": 0,
            "max_orch_per_round": 0,
        },
    )
    assert lgb.begin_round(pid, ["file_read"])["status"] == "allow"
    assert lgb.begin_round(pid, ["file_read"])["status"] == "allow"
    d = lgb.begin_round(pid, ["file_read"])
    assert d["status"] == "force_final"
    assert d["code"] == "max_tool_rounds"


def test_local_worker_ban_crew():
    pid = "test-pid-crew"
    lgb.configure_for_process(
        pid,
        {
            "workforce": True,
            "role_kind": "implement",
            "ban_worker_orch": True,
            "max_tool_rounds": 20,
            "max_crew_total": 0,
        },
    )
    d = lgb.pre_tool(pid, "crew_steward", {"action": "assign"})
    assert d["status"] == "block"
    assert d["code"] == "worker_orch_banned"
    assert "禁止" in d["message"]


def test_local_truncated_reread_blocked():
    pid = "test-pid-trunc"
    lgb.configure_for_process(
        pid,
        {
            "workforce": True,
            "role_kind": "implement",
            "ban_worker_orch": True,
            "max_tool_rounds": 20,
        },
    )
    lgb.post_tool(
        pid,
        "file_read",
        {"path": "a.py"},
        result="x" * 50 + "\n...[100 chars omitted for LLM context; tool=file_read]...\n" + "y" * 20,
        truncated=True,
    )
    d = lgb.pre_tool(pid, "file_read", {"path": "a.py"})
    assert d["status"] == "block"
    assert d["code"] == "truncated_reread_blocked"
    d2 = lgb.pre_tool(pid, "file_read", {"path": "a.py", "offset": 40})
    assert d2["status"] == "allow"


def test_force_final_messages():
    assert "轮次" in lgb.force_final_message("max_tool_rounds")
    assert "85%" in lgb.force_final_message("budget_ratio") or "预算" in lgb.force_final_message(
        "budget_ratio"
    )
    assert "编制" in lgb.force_final_message("orch_window_thrash")


def test_orchestration_cap_default_one():
    from types import SimpleNamespace

    from backend.agent.decisive import orchestration_cap_results

    calls = [
        SimpleNamespace(name="crew_steward", id=f"c{i}", arguments={"name": f"e{i}"})
        for i in range(3)
    ]
    capped = orchestration_cap_results(calls, max_orch=1)
    assert set(capped.keys()) == {"c1", "c2"}
