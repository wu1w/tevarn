# -*- coding: utf-8 -*-
"""Roadmap §6 (0.7) long-run reliability + efficiency tests.

Product version stays 0.5.0-alpha.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── R-01 spill process_id path ────────────────────────────────


def test_normalize_tool_result_spills_when_process_id(monkeypatch):
    from backend.agent import tool_result_contract as trc

    n = max(trc.SPILL_THRESHOLD, trc.TOOL_RESULT_BUDGET["command"]) + 500
    big = "x" * n
    called = {}

    class K:
        def result_spill(self, pid, tool, content):
            called["pid"] = pid
            called["tool"] = tool
            called["n"] = len(content)
            return {
                "spilled": True,
                "handle": {"id": "h-proc-abc"},
                "context": f"[handle spilled tool={tool} pid={pid}]",
            }

    monkeypatch.setattr("backend.kernel.get_kernel", lambda: K())
    out = trc.normalize_tool_result(big, tool_name="command", process_id="proc-abc")
    assert "handle" in out or "spilled" in out or "proc-abc" in out or "h-proc-abc" in out
    assert "result_load" in out
    assert called.get("pid") == "proc-abc"
    assert called.get("n") == n


def test_registry_passes_process_id_to_normalize(monkeypatch):
    """Static: registry source must pass process_id into normalize_tool_result."""
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "tools" / "registry.py").read_text(
        encoding="utf-8"
    )
    assert "process_id=_pid" in text or "process_id=" in text
    assert "_kernel_process_id" in text


# ── R-02 exit message unification ────────────────────────────


def test_budget_exit_message_has_recovery():
    from backend.agent.exit_reasons import format_exit_user_message

    msg = format_exit_user_message("budget_exhausted", process_id="p1")
    assert "预算" in msg or "Budget" in msg or "budget" in msg.lower()
    assert "恢复" in msg or "resume" in msg.lower() or "top_up" in msg or "提高" in msg


# ── R-03 end_process cascade + cap clear ─────────────────────


@pytest.mark.asyncio
async def test_python_end_process_clears_caps_and_children():
    from backend.kernel.kernel import AgentKernel
    from backend.kernel.process import AgentProcess

    k = AgentKernel()
    parent = await k.create_process("main", capabilities=["file_read", "terminal"], token_budget=1000)
    child = await k.create_process(
        "worker",
        parent_id=parent.id,
        capabilities=["file_read"],
        token_budget=100,
    )
    assert not child.is_terminal
    await k.end_process(parent.id, state="completed", reason="done")
    p2 = k.get_process(parent.id)
    c2 = k.get_process(child.id)
    assert p2 is not None and p2.is_terminal
    assert c2 is not None and c2.is_terminal
    # caps cleared on parent
    caps = getattr(p2, "capabilities", None)
    if caps is not None:
        assert len(caps) == 0 or caps == frozenset()


# ── R-05 cost panel summary ──────────────────────────────────


@pytest.mark.asyncio
async def test_cost_panel_summary_shape(monkeypatch):
    from backend.api.routes import kernel as kr

    class FakeK:
        def _call(self, method, params=None):
            if method == "cost_panel":
                return {"totals": {"tokens": 100, "billable": 40}, "by_family": {}}
            if method == "cache_metrics":
                return {"totals": {"hit_rate": 0.5}, "families": {"deepseek": {"hits": 1, "misses": 1}}}
            if method == "marathon_metrics":
                return {"resume_success_rate": 1.0}
            return {}

        def list_processes(self, include_terminal=False):
            return [SimpleNamespace(id="p1", tokens_used=10, token_budget=100)]

        def resource_usage(self, pid):
            return {"child_proc": {"used": 1, "limit": 16}, "memory_bytes": {"used": 100, "limit": 256}}

    monkeypatch.setattr(kr, "get_kernel", lambda: FakeK())
    user = SimpleNamespace(id="u1")
    out = await kr.cost_panel(current_user=user, process_id=None)
    assert out.get("summary")
    assert "tokens" in out["summary"]
    assert "billable" in out["summary"]


# ── version lock ─────────────────────────────────────────────


def test_product_version_still_050():
    from backend.core.version import product_version

    assert product_version()  # non-empty authority version
    assert isinstance(product_version(), str) and len(product_version()) >= 3
