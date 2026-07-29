"""Unit tests for harness P0–P2 pure modules (no live LLM)."""

from __future__ import annotations

import pytest

from backend.agent.permission_rules_dsl import parse_rule_string, rules_from_payload
from backend.agent.dangerous_paths import secret_deny_rules
from backend.kernel.process_identity import process_belongs_to, sum_tokens_for_agent, workforce_key
from backend.agent.plan_session import (
    approve_plan,
    requires_plan_approval,
    start_plan,
    submit_plan_markdown,
)
from backend.agent.subagent_types import resolve_type, SPECS
from backend.computer.profiles import resolve_profile, list_profiles
from backend.kernel.workflow_runner import WorkflowBudgetExceeded, WorkflowRunner


def test_parse_bash_deny():
    r = parse_rule_string("Bash(rm*)", "deny")
    assert r is not None
    assert r.key == "bash"
    assert r.decision == "deny"
    assert r.pattern == "rm*"


def test_rules_from_payload_order():
    rules = rules_from_payload(
        {"allow": ["Read"], "deny": ["Bash(sudo*)"], "ask": ["Edit"]}
    )
    assert any(r.decision == "deny" for r in rules)
    assert rules[-1].decision == "deny" or any(r.pattern == "sudo*" for r in rules)


def test_secret_deny_includes_env_and_pem():
    rules = secret_deny_rules()
    patterns = {r.pattern for r in rules}
    assert any(".env" in p or p.endswith(".env") or p == "*.env" for p in patterns)
    assert any("pem" in p for p in patterns)


def test_process_identity_matching():
    aid = "31ae5c78-1596-489d-ad1b-4d76cc3d9eb9"
    assert process_belongs_to(workforce_key(aid), agent_id=aid)
    assert process_belongs_to(f"wf:{aid}", agent_id=aid, agent_name="金算")
    assert process_belongs_to("金算", agent_id=aid, agent_name="金算")
    assert not process_belongs_to("wf:other", agent_id=aid)
    procs = [
        {"identity": f"wf:{aid}", "tokens_used": 100},
        {"identity": "main", "tokens_used": 50},
    ]
    assert sum_tokens_for_agent(procs, agent_id=aid) == 100


def test_plan_gate_approval_cycle():
    sid = "test-plan-session-1"
    start_plan(session_id=sid)
    assert requires_plan_approval(session_id=sid, chat_mode="plan")
    submit_plan_markdown(
        "# Fix auth\n1. Update login\n2. Add tests\n",
        session_id=sid,
    )
    assert requires_plan_approval(session_id=sid, chat_mode="plan")
    approve_plan(session_id=sid)
    assert not requires_plan_approval(session_id=sid, chat_mode="")


def test_subagent_types_catalog():
    assert set(SPECS) >= {"explore", "implement", "review", "general"}
    assert resolve_type("explore").chat_mode == "plan"
    assert resolve_type("implement").worktree is True
    assert resolve_type("nope").kind == "general"


def test_sandbox_profiles():
    ids = {p["id"] for p in list_profiles()}
    assert "workspace" in ids and "strict" in ids and "read_only" in ids
    assert resolve_profile("strict").network is False
    assert resolve_profile("off").prefer_backend == "local"


@pytest.mark.asyncio
async def test_workflow_budget_blocks():
    runner = WorkflowRunner(session_id="00000000-0000-0000-0000-000000000001", agent_budget=1)

    async def fake_run_agent(role, goal, context=""):
        runner._consume(1)
        return "ok"

    runner._run_agent = fake_run_agent  # type: ignore[method-assign]
    out = await runner.run(
        {
            "name": "t",
            "agent_budget": 1,
            "steps": [
                {"type": "agent", "role": "explore", "goal": "a"},
                {"type": "agent", "role": "explore", "goal": "b"},
            ],
        }
    )
    assert out["ok"] is False
    assert "budget" in (out.get("error") or "").lower() or out["agent_used"] >= 1
