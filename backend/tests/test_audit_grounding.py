"""Audit grounding: completion gate + path phantom footer."""

from __future__ import annotations

from pathlib import Path

from backend.agent.audit_grounding import (
    annotate_audit_report,
    extract_cited_paths,
    is_audit_like_task,
    maybe_annotate_audit_report,
)
from backend.agent.completion_gate import evaluate_completion


def test_is_audit_like_task():
    assert is_audit_like_task("对 backend 做一轮安全审计")
    assert is_audit_like_task("code review the agent loop")
    assert not is_audit_like_task("把按钮颜色改成蓝色")


def test_audit_gate_blocks_no_tools():
    v = evaluate_completion("请审计 backend 竞态与泄漏", [], "全是 Critical")
    assert not v.ok
    assert v.reason == "audit_no_tools"


def test_audit_gate_soft_allows_shallow_glob():
    """Soft default: shallow tools no longer hard-force; footer catches phantoms."""
    v = evaluate_completion(
        "第二轮代码审计",
        ["glob", "glob"],
        "发现 orchestrator 锁问题",
    )
    # soft: ok (soft_* reason) — strong models not trapped in tool ritual
    assert v.ok
    assert "soft" in v.reason or v.reason == "ok"


def test_audit_gate_strict_blocks_shallow_glob(monkeypatch):
    from backend.agent.grounding_policy import clear_policy_cache

    monkeypatch.setenv("TAKTON_GROUNDING_MODE", "strict")
    clear_policy_cache()
    try:
        v = evaluate_completion(
            "第二轮代码审计",
            ["glob", "glob"],
            "发现 orchestrator 锁问题",
        )
        assert not v.ok
        assert v.reason in (
            "audit_shallow_tools",
            "audit_report_without_evidence",
            "audit_list_only",
            "audit_few_deep",
            "only_glob",
        )
    finally:
        clear_policy_cache()


def test_audit_gate_accepts_enough_reads():
    v = evaluate_completion(
        "审计 kernel 权限",
        ["glob", "grep", "file_read", "file_read", "grep"],
        "见 backend/kernel/identity.py 的 cascade 说明",
        max_followups_done=0,
    )
    assert v.ok


def test_extract_and_annotate_missing_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # only create one real file
    real = tmp_path / "backend" / "real_mod.py"
    real.parent.mkdir(parents=True)
    real.write_text("x=1\n", encoding="utf-8")

    report = (
        "# 审计\n"
        "🔴 High `backend/orchestrator.py` 有锁问题\n"
        "🟡 Medium `backend/real_mod.py` 还行\n"
    )
    out = annotate_audit_report(
        report,
        user_input="请做代码审计",
        tools_used=["file_read"],
        force=True,
    )
    assert "落地校验" in out
    assert "orchestrator.py" in out
    assert "不存在" in out or "❌" in out


def test_maybe_annotate_skips_normal_chat():
    text = "按钮已改成蓝色。"
    assert maybe_annotate_audit_report("改颜色", text, ["file_write"]) == text


def test_extract_paths_basic():
    paths = extract_cited_paths(
        "see `backend/agent/loop.py:120` and backend/kernel/identity.py"
    )
    assert any("loop.py" in p for p in paths)
    assert any("identity.py" in p for p in paths)
