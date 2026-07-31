"""Next-round T1–T10 smoke tests (lightweight, no process killing)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def test_default_execution_mode_is_sandbox():
    from backend.agent.working_mode import DEFAULT_EXECUTION_MODE, resolve_execution_mode

    assert DEFAULT_EXECUTION_MODE == "sandbox"
    assert resolve_execution_mode("sandbox") == "sandbox"
    assert resolve_execution_mode("local") == "local"


def test_config_next_round_flags():
    from backend.core.config import settings

    assert hasattr(settings, "agent_kernel_run_gate_required")
    assert hasattr(settings, "agent_court_rust_required")
    assert hasattr(settings, "agent_package_market_url")


def test_sdk_pack_validate_and_zip(tmp_path):
    agent = tmp_path / "demo"
    agent.mkdir()
    (agent / "entry.py").write_text("print('hi')\n", encoding="utf-8")
    (agent / "agent.json").write_text(
        json.dumps(
            {
                "name": "demo",
                "version": "0.1.0",
                "entry": "entry.py",
                "permissions": ["file_read", "terminal"],
                "resources": {"isolation": "interactive"},
            }
        ),
        encoding="utf-8",
    )
    from scripts.takton_sdk_pack import pack_zip, validate

    meta = validate(agent)
    assert meta["ok"] is True
    assert "terminal" in meta["risky_permissions"]
    z = pack_zip(agent, tmp_path / "out", meta)
    assert z.is_file()


def test_ci_eval_gate_soft_without_artifact(tmp_path, monkeypatch):
    from backend.services import weekly_report as wr
    import scripts.ci_eval_gate as gate

    monkeypatch.setattr(wr, "eval_data_dir", lambda: tmp_path)
    (tmp_path / "runs").mkdir()
    (tmp_path / "weekly").mkdir()
    monkeypatch.setattr(sys, "argv", ["ci_eval_gate.py"])
    assert gate.main() == 0


@pytest.mark.asyncio
async def test_tool_gate_charges_io_for_large_args(monkeypatch):
    from backend.kernel import tool_gate

    monkeypatch.setattr(tool_gate, "_kernel_enabled", lambda: True)
    charges: list[tuple[str, int]] = []

    class FakeK:
        async def mediate(self, *a, **k):
            return {"allowed": True}

        def resource_charge(self, pid, kind, amount=1):
            charges.append((kind, int(amount)))
            return 1

    monkeypatch.setattr("backend.kernel.get_kernel", lambda: FakeK())
    args, err = await tool_gate.enforce_tool_gate(
        "file_write",
        {"_kernel_process_id": "p1", "blob": "y" * 5000},
    )
    assert err is None
    kinds = [c[0] for c in charges]
    assert "tool_calls" in kinds
    assert "io_write_bytes" in kinds


def test_dashboard_and_collab_routes_exist():
    from backend.api.routes import kernel as kr

    assert hasattr(kr, "kernel_dashboard")
    assert hasattr(kr, "sandbox_coverage")
    assert hasattr(kr, "collab_get")
    assert hasattr(kr, "collab_interrupt")


def test_remote_catalog_rejects_non_https():
    from backend.packages.market import _fetch_remote_catalog

    assert _fetch_remote_catalog("http://evil.example/x") == []
    assert _fetch_remote_catalog("") == []
