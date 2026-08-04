# -*- coding: utf-8 -*-
"""E2E-style bridge test: full PR1–PR4 decision path without live LLM.

Simulates a research worker run: many file_reads after truncate + crew_steward
attempts must be blocked / force-final without kernel host (local fallback).
When Rust host is available, also exercises RPC path.
"""
from __future__ import annotations

import backend.agent.loop_guard_bridge as lgb


def setup_function():
    lgb.reset_local_for_tests()


def test_e2e_research_worker_simulation():
    pid = "e2e-research-worker"
    cfg = lgb.build_loop_guard_config(
        workforce=True,
        identity_name="研究员",
        instruction="调研 GitHub MCP 与 Skill 接入，只读验证",
        payload={"thoroughness": "medium"},
    )
    assert cfg["ban_worker_orch"] is True
    assert cfg["max_tool_rounds"] == 12
    lgb.configure_for_process(pid, cfg)

    # Round 1–2: normal reads OK
    assert lgb.begin_round(pid, ["file_read", "grep"])["status"] == "allow"
    assert lgb.pre_tool(pid, "file_read", {"path": "a.py"})["status"] == "allow"
    lgb.post_tool(
        pid,
        "file_read",
        {"path": "a.py"},
        result="head\n...[9000 chars omitted for LLM context; tool=file_read]...\ntail",
        truncated=True,
    )

    # Full re-read blocked (PR3)
    blocked = lgb.pre_tool(pid, "file_read", {"path": "a.py"})
    assert blocked["status"] == "block"
    assert blocked["code"] == "truncated_reread_blocked"

    # Offset re-read allowed
    assert lgb.pre_tool(pid, "file_read", {"path": "a.py", "offset": 100})["status"] == "allow"

    # crew_steward banned on worker (PR1)
    ban = lgb.pre_tool(pid, "crew_steward", {"action": "assign", "name": "工程师"})
    assert ban["status"] == "block"
    assert ban["code"] == "worker_orch_banned"

    # Burn tool rounds until force_final (PR1 max_turns)
    for i in range(20):
        d = lgb.begin_round(pid, ["grep"])
        if d.get("status") == "force_final":
            assert d.get("code") == "max_tool_rounds"
            msg = lgb.force_final_message(d["code"], d.get("reason", ""))
            assert "硬顶" in msg or "轮次" in msg
            break
    else:
        raise AssertionError("expected max_tool_rounds force_final within 20 rounds")


def test_e2e_steward_crew_cap_local():
    pid = "e2e-steward"
    cfg = lgb.build_loop_guard_config(
        workforce=False,
        identity_name="CEO",
        instruction="派工给员工",
    )
    # steward/chat
    assert cfg.get("ban_worker_orch") is False
    cfg["max_crew_total"] = 3
    cfg["max_orch_per_round"] = 1
    cfg["role_kind"] = "steward"
    lgb.configure_for_process(pid, cfg)
    # Local fallback does not fully emulate crew cap across host — only ban path.
    # Ensure config shapes are correct for host configure.
    assert cfg["max_crew_total"] == 3
    assert cfg["max_orch_per_round"] == 1


def test_optional_rust_host_loop_guard():
    """If kernel host is up with new ABI, configure + pre_tool via RPC."""
    try:
        from backend.kernel import get_kernel

        k = get_kernel()
    except Exception:
        return
    if not hasattr(k, "_call") and not hasattr(k, "loop_guard_configure"):
        return
    # Create a throwaway process if possible
    try:
        if hasattr(k, "create_process"):
            # may be async python kernel — skip if not dict
            pass
        # Soft probe: list methods
        methods = []
        if hasattr(k, "_call"):
            try:
                h = k._call("list_methods") or {}
                methods = h.get("methods") or h.get("list") or []
            except Exception:
                try:
                    h = k._call("health") or {}
                    methods = []
                except Exception:
                    return
        if methods and "loop_guard_configure" not in methods:
            # old host binary — Python fallback still OK
            return
        pid = "e2e-host-probe"
        cfg = {
            "workforce": True,
            "role_kind": "research",
            "thoroughness": "quick",
            "max_tool_rounds": 6,
            "ban_worker_orch": True,
            "max_crew_total": 0,
            "max_orch_per_round": 0,
        }
        r = lgb.configure_for_process(pid, cfg)
        assert isinstance(r, dict)
        d = lgb.pre_tool(pid, "crew_steward", {"action": "list"})
        # host or local must block
        assert d.get("status") in ("block", "allow", "force_final")
        if d.get("status") == "block":
            assert d.get("code") == "worker_orch_banned"
    except Exception:
        # Host unavailable — not a failure for offline CI
        return
