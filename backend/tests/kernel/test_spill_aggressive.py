"""Spill policy: soft inline budgets + rich envelope (not infinite number raise)."""

from __future__ import annotations


def test_budgets_support_analysis_not_toy_800() -> None:
    from backend.agent.tool_result_contract import (
        DEFAULT_TOOL_BUDGET,
        SPILL_PREVIEW_CHARS,
        SPILL_THRESHOLD,
        TOOL_RESULT_BUDGET,
    )

    assert SPILL_THRESHOLD >= 12_000
    assert DEFAULT_TOOL_BUDGET >= 8_000
    # Aggressive H2/P1 defaults: prefer spill handle over fat inline context.
    assert TOOL_RESULT_BUDGET["python"] >= 1_000
    assert TOOL_RESULT_BUDGET["command"] >= 8_000
    assert SPILL_PREVIEW_CHARS >= 4_000


def test_weather_size_stays_inline(monkeypatch) -> None:
    from backend.agent import tool_result_contract as trc

    def _boom(*_a, **_k):
        raise AssertionError("must not spill under soft budget")

    class FakeK:
        result_spill = staticmethod(_boom)

    monkeypatch.setattr("backend.kernel.get_kernel", lambda: FakeK())
    body = "date: 2026-08-08\n" + ("hourly line xxxxxxxx\n" * 40)
    assert len(body) < trc.TOOL_RESULT_BUDGET["python"]
    out = trc.normalize_tool_result(body, tool_name="python", process_id="p1")
    assert "tool_result_handle" not in out
    assert out == body


def test_large_spill_uses_rich_envelope(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    from backend.agent import tool_result_contract as trc

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(tmp_path)))

    class FakeK:
        def result_spill(self, pid, tool, content):
            return {
                "spilled": True,
                "handle": {"id": "abc123deadbeef"},
                # Kernel may return tiny preview — Python must enrich
                "context": "[tool_result_handle id=abc123deadbeef]\npreview:\ntiny",
            }

    monkeypatch.setattr("backend.kernel.get_kernel", lambda: FakeK())
    n = max(trc.SPILL_THRESHOLD, trc.TOOL_RESULT_BUDGET["python"]) + 100
    text = ("LINE-%05d-payload\n" % i for i in range(n))
    text = "".join(text)[:n]
    assert len(text) >= n
    out = trc.normalize_tool_result(text, tool_name="python", process_id="p1")
    assert "tool_result_handle id=abc123deadbeef" in out
    assert "result_load" in out
    assert "Do NOT re-run" in out or "不要" in out or "do NOT" in out.lower()
    # preview budget much larger than old 240
    assert len(out) > 2000


def test_format_spill_envelope_head_tail() -> None:
    from backend.agent.tool_result_contract import format_spill_envelope

    text = "HEAD" + ("m" * 20_000) + "TAIL"
    env = format_spill_envelope(handle_id="h1", tool_name="python", full_text=text)
    assert "id=h1" in env
    assert "HEAD" in env
    assert "TAIL" in env
    assert "result_load" in env
