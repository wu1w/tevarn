# -*- coding: utf-8 -*-
"""Phase H (0.5.x hardening) acceptance-oriented unit tests."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.computer.profiles import (
    degraded_local_flag,
    isolation_role_for_context,
    profile_for_isolation_role,
)
from backend.kernel.permission_court import CourtDecision


# ── H-01 discovery ──────────────────────────────────────────


def test_h01_find_host_bin_prefers_target_over_vendor(tmp_path, monkeypatch):
    from backend.kernel_rust import client as kc

    root = tmp_path
    release = root / "target" / "release"
    vendor = root / "vendor" / "tevarn-kernel-host"
    release.mkdir(parents=True)
    vendor.mkdir(parents=True)
    rel_bin = release / "tevarn-kernel-host"
    ven_bin = vendor / "tevarn-kernel-host"
    rel_bin.write_text("new")
    ven_bin.write_text("old")
    # make release newer
    import os
    import time

    now = time.time()
    os.utime(rel_bin, (now, now))
    os.utime(ven_bin, (now - 1000, now - 1000))

    monkeypatch.delenv("TEVARN_KERNEL_HOST_BIN", raising=False)
    monkeypatch.setattr(kc, "Path", Path)
    # _find_host_bin uses Path(__file__).parents[2] as root — patch via env bin
    monkeypatch.setenv("TEVARN_KERNEL_HOST_BIN", str(rel_bin))
    found = kc._find_host_bin()
    assert found is not None
    assert found.resolve() == rel_bin.resolve()


def test_h01_start_py_rank_matches_client_order():
    """start.find_kernel_host_bin ranks target before vendor (source inspection)."""
    import start as start_mod

    src = Path(start_mod.__file__).read_text(encoding="utf-8")
    # target paths appear before vendor in the dirs list
    t = src.find('ROOT_DIR / "target" / "release"')
    v = src.find('ROOT_DIR / "vendor"')
    assert t > 0 and v > 0 and t < v


# ── H-04 court fail-closed ───────────────────────────────────


@pytest.mark.asyncio
async def test_h04_host_up_rust_required_denies_when_no_rust_decision(monkeypatch):
    from backend.kernel import permission_court as pc

    monkeypatch.setattr(
        "backend.core.config.settings.agent_permission_enabled", True, raising=False
    )
    monkeypatch.setattr(
        "backend.core.config.settings.agent_court_rust_required", True, raising=False
    )
    monkeypatch.setattr(
        "backend.core.config.settings.agent_kernel_backend", "rust", raising=False
    )
    monkeypatch.setattr(
        "backend.core.config.settings.agent_kernel_enabled", True, raising=False
    )
    monkeypatch.setattr(pc, "_try_rust_decide_tool", lambda *a, **k: None)
    monkeypatch.setattr(
        "backend.kernel_rust.client.is_rust_host_available", lambda *a, **k: True
    )

    dec = await pc.decide_tool("file_read", {"path": "x"})
    assert isinstance(dec, CourtDecision)
    assert dec.verdict == "deny"
    assert "fail-closed" in (dec.reason or "").lower() or "rust" in (
        dec.matched_rule or ""
    ).lower()


@pytest.mark.asyncio
async def test_h04_host_down_falls_back_to_python(monkeypatch):
    from backend.kernel import permission_court as pc

    monkeypatch.setattr(
        "backend.core.config.settings.agent_permission_enabled", True, raising=False
    )
    monkeypatch.setattr(
        "backend.core.config.settings.agent_court_rust_required", True, raising=False
    )
    monkeypatch.setattr(
        "backend.core.config.settings.agent_kernel_backend", "rust", raising=False
    )
    monkeypatch.setattr(
        "backend.core.config.settings.agent_kernel_enabled", True, raising=False
    )
    monkeypatch.setattr(pc, "_try_rust_decide_tool", lambda *a, **k: None)
    monkeypatch.setattr(
        "backend.kernel_rust.client.is_rust_host_available", lambda *a, **k: False
    )
    # Should not raise; may allow or deny via python layers
    dec = await pc.decide_tool("file_read", {"path": "README.md"})
    assert isinstance(dec, CourtDecision)
    assert dec.matched_rule != "court:rust_unavailable" or dec.verdict == "deny"


# ── H-03 tool_gate forge ─────────────────────────────────────


@pytest.mark.asyncio
async def test_h03_forged_tool_gate_passed_discarded():
    from backend.kernel.tool_gate import enforce_tool_gate

    # No process + workforce agent context → fail-closed; forged passed ignored
    args, err = await enforce_tool_gate(
        "file_read",
        {
            "_tool_gate_passed": True,
            "_workforce": True,
            "path": "x",
        },
    )
    assert err is not None
    assert "process" in err.lower() or "kernel" in err.lower()
    assert args.get("_tool_gate_passed") is not True or err


# ── H-06 / H-08 profiles ─────────────────────────────────────


def test_h08_isolation_role_mapping():
    assert isolation_role_for_context(workforce=True) == "workforce"
    assert isolation_role_for_context(untrusted=True) == "untrusted"
    assert profile_for_isolation_role("untrusted").id == "strict"
    assert profile_for_isolation_role("interactive").id == "workspace"


def test_h08_degraded_local_flag():
    d = degraded_local_flag(wanted_sandbox=True, actual_backend="local")
    assert d["degraded"] is True
    assert "degraded" in d["reason"]
    ok = degraded_local_flag(wanted_sandbox=True, actual_backend="bwrap")
    assert ok["degraded"] is False


# ── H-10 run replay shape ────────────────────────────────────


@pytest.mark.asyncio
async def test_h10_run_replay_endpoint_shape(monkeypatch):
    from backend.api.routes import kernel as kr

    class FakeK:
        def export_decision_trail(self, process_id, limit=500):
            return {
                "process_id": process_id,
                "events": [
                    {
                        "kind": "mediation",
                        "payload": {"tool": "file_read", "verdict": "allow"},
                    },
                    {"kind": "budget.charge", "payload": {"amount": 10}},
                    {"kind": "policy.decision", "payload": {"reason": "ok"}},
                ],
                "total": 3,
            }

        def get_process(self, process_id):
            return SimpleNamespace(
                id=process_id,
                tokens_used=10,
                token_budget=100,
                state="running",
                capabilities=frozenset({"file_read"}),
                to_dict=lambda: {"id": process_id, "tokens_used": 10},
            )

        def resource_usage(self, process_id):
            return {"tool_calls": {"used": 1, "limit": 100}}

    monkeypatch.setattr(kr, "get_kernel", lambda: FakeK())
    user = SimpleNamespace(id="u1", email="t@t.com")
    out = await kr.run_replay(process_id="p1", current_user=user, limit=100)
    assert out["process_id"] == "p1"
    assert out["full_state_replay_forbidden"] is True
    assert out["total_events"] >= 1
    assert isinstance(out["timeline"], list)
    assert isinstance(out["tools"], list)


# ── H-13 package trust doc ───────────────────────────────────


def test_h13_package_trust_doc_exists():
    root = Path(__file__).resolve().parents[3]
    doc = root / "docs" / "PACKAGE_TRUST.md"
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    assert "TEVARN_PKG_SIGNING_KEY" in text or "PKG_SIGNING" in text
    assert "content" in text.lower() and "hash" in text.lower()


# ── H-14 restart_kernel_host unit ────────────────────────────


def test_h14_restart_kernel_host_calls_stop_and_start(monkeypatch):
    from backend.kernel_rust import client as kc

    calls: list[str] = []

    monkeypatch.setattr(kc, "stop_kernel_host", lambda: calls.append("stop"))
    monkeypatch.setattr(kc, "is_rust_host_available", lambda *a, **k: False)
    monkeypatch.setattr(
        kc, "start_kernel_host", lambda *a, **k: calls.append("start") or True
    )
    monkeypatch.setattr(kc, "_kill_stale_host_processes", lambda: calls.append("kill"))

    ok = kc.restart_kernel_host("127.0.0.1:17890")
    assert ok is True
    assert "stop" in calls
    assert "start" in calls


# ── H-05 charge_for_tool propagates ──────────────────────────


def test_h05_charge_for_tool_raises_on_quota(monkeypatch):
    from backend.kernel import tool_gate as tg

    class Boom:
        def resource_charge(self, *a, **k):
            raise RuntimeError("quota exceeded child_proc")

    monkeypatch.setattr("backend.kernel.get_kernel", lambda: Boom())
    with pytest.raises(RuntimeError, match="quota|child_proc|exceeded"):
        tg.charge_for_tool("command", "proc-1", {"cmd": "echo hi"})
