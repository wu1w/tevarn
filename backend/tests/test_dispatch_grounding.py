"""CEO dispatch grounding — multi-class hallucination on assign (soft-default)."""

from __future__ import annotations

import pytest

from backend.agent.completion_gate import evaluate_completion
from backend.agent.dispatch_grounding import (
    format_block_message,
    format_warn_message,
    scan_dispatch_instruction,
    scan_report_hallucination_flags,
    worker_hygiene_block,
)
from backend.agent.grounding_policy import clear_policy_cache, get_policy
from backend.agent.task_grounding import (
    annotate_grounded_report,
    grounding_prompt_block,
)


@pytest.fixture(autouse=True)
def _soft_mode(monkeypatch):
    monkeypatch.setenv("TAKTON_GROUNDING_MODE", "soft")
    clear_policy_cache()
    yield
    clear_policy_cache()


def test_block_phantom_path():
    r = scan_dispatch_instruction(
        "请审计 `backend/agent/orchestrator.py` 的锁问题并修复"
    )
    assert r.severity == "block"
    assert r.ok is False
    assert r.missing_paths or r.phantom_names
    msg = format_block_message(r)
    assert "落地校验" in msg or "Error" in msg


def test_block_template_module_basename():
    r = scan_dispatch_instruction(
        "请打开 session_manager.py 和 tool_execution.py 分析竞态"
    )
    assert r.severity == "block"
    assert r.phantom_names


def test_soft_warn_hard_metric_not_block():
    """Soft default: metrics are warn, not hard block (model keeps agency)."""
    r = scan_dispatch_instruction(
        "请把转化率做到必须达到 95%，并写进报告结论"
    )
    assert r.severity == "warn"
    assert r.ok is True
    assert any("metric" in x or "hard" in x for x in r.reasons)
    assert "提示" in format_warn_message(r) or "warn" in format_warn_message(r)


def test_soft_warn_hard_count_vulns():
    r = scan_dispatch_instruction(
        "仓库里共有 128 个高危漏洞，请按优先级修复"
    )
    assert r.severity == "warn"
    assert r.ok is True


def test_soft_warn_stack_trace():
    r = scan_dispatch_instruction(
        '现场如下 Traceback (most recent call last):\n'
        '  File "app.py", line 12, in <module>\n'
        "请据此修复"
    )
    assert r.severity == "warn"
    assert r.ok is True


def test_soft_warn_many_cves():
    r = scan_dispatch_instruction(
        "确认存在 CVE-2024-12345 与 CVE-2023-99999，必须立即打补丁"
    )
    assert r.severity in ("warn", "ok")
    assert r.ok is True


def test_strict_blocks_metrics(monkeypatch):
    monkeypatch.setenv("TAKTON_GROUNDING_MODE", "strict")
    clear_policy_cache()
    r = scan_dispatch_instruction("转化率必须达到 95%")
    assert r.severity == "block"
    assert r.ok is False


def test_warn_latest_with_verify_room():
    r = scan_dispatch_instruction(
        "请调研最新模型发布动态（自行 web_search 核实，不要写死结论）"
    )
    assert r.severity in ("ok", "warn")
    assert r.ok is True


def test_clean_goal_scope_ok(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "kernel").mkdir()
    r = scan_dispatch_instruction(
        "在 backend/kernel 目录下自行探路，审计权限与进程预算相关实现；"
        "不要假设具体文件名，路径不存在则报告。"
    )
    assert r.severity in ("ok", "warn")
    assert r.ok is True


def test_ceo_dispatch_soft_allows_without_evidence():
    """Soft: CEO-only assign is not force-followed; assign gate still protects."""
    v = evaluate_completion(
        "请审计整个 backend 安全问题",
        ["crew_steward"],
        "已派给金算和工程师",
    )
    assert v.ok


def test_ceo_dispatch_with_read_ok():
    v = evaluate_completion(
        "请审计 backend",
        ["grep", "file_read", "crew_steward"],
        "已按真实路径派单",
    )
    assert v.ok


def test_worker_hygiene_short():
    h = worker_hygiene_block()
    assert "工单" in h
    assert len(h) < 400  # short, not a ritual wall


def test_prompt_block_short():
    b = grounding_prompt_block()
    assert "Evidence" in b or "证据" in b or "tools" in b.lower()
    assert len(b) < 600


def test_report_flags_certainty():
    flags = scan_report_hallucination_flags(
        "根因就是配置错误，毫无疑问，转化率必须达到 99%"
    )
    assert flags


def test_footer_still_soft_annotates():
    out = annotate_grounded_report(
        "一定是 Redis 挂了导致的超时。",
        user_input="为什么接口超时了排查一下",
        tools_used=[],
    )
    assert "落地校验" in out


def test_policy_strong_model_relaxes():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("TAKTON_GROUNDING_MODE", "balanced")
    clear_policy_cache()
    p = get_policy("claude-opus-4")
    assert p.mode == "soft"  # strong → one step softer
    monkeypatch.undo()
    clear_policy_cache()


def test_to_dict_stable():
    r = scan_dispatch_instruction("空口无凭请自行 glob 探路 backend/")
    d = r.to_dict()
    assert "severity" in d
    assert "missing_paths" in d
