"""Aggressive result spill defaults."""

from __future__ import annotations


def test_spill_threshold_default_is_aggressive() -> None:
    from backend.agent.tool_result_contract import SPILL_THRESHOLD, DEFAULT_TOOL_BUDGET

    assert SPILL_THRESHOLD <= 1200
    assert DEFAULT_TOOL_BUDGET <= 1000


def test_normalize_spills_large_without_pid(monkeypatch) -> None:
    """Orphan path still attempts spill (pid=orphan)."""
    from backend.agent import tool_result_contract as trc

    class FakeK:
        def result_spill(self, pid, tool, content):
            assert pid == "orphan"
            assert len(content) >= trc.SPILL_THRESHOLD
            return {
                "spilled": True,
                "context": f"[tool_result_handle id=x tool={tool}]",
            }

    monkeypatch.setattr(
        "backend.kernel.get_kernel",
        lambda: FakeK(),
    )
    out = trc.normalize_tool_result(
        "y" * (trc.SPILL_THRESHOLD + 50),
        tool_name="command",
        process_id=None,
    )
    assert "tool_result_handle" in out
