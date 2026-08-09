"""Workforce per-job budget lift + budget-fail classification."""

from __future__ import annotations

from types import SimpleNamespace

from backend.agent.workforce_budget import (
    budget_for_identity,
    is_budget_exceeded_result,
    suggested_token_budget,
)


def test_audit_lifts_30k_to_120k():
    b = suggested_token_budget(
        base=30_000,
        instruction="请对 backend 做安全审计，file_read 逐文件",
        role="安全审计员",
        name="安澜",
    )
    assert b is not None
    assert b >= 120_000


def test_role_audit_lifts_even_if_instruction_vague():
    b = suggested_token_budget(
        base=30_000,
        instruction="按分配范围检查代码",
        role="架构审计员",
        name="钟离",
    )
    assert b is not None
    assert b >= 120_000


def test_simple_job_keeps_low_budget():
    b = suggested_token_budget(
        base=8_000,
        instruction="说一声你好",
        role="助手",
    )
    assert b == 8_000


def test_none_base_uses_fallback_then_lift():
    b = suggested_token_budget(
        base=None,
        instruction="审计 permissions.py",
        role="安全审计员",
    )
    assert b is not None
    assert b >= 120_000


def test_explicit_zero_unlimited():
    assert suggested_token_budget(base=0, instruction="审计") == 0


def test_budget_for_identity():
    ident = SimpleNamespace(
        default_token_budget=30_000,
        role="逻辑审计员",
        name="凌远",
    )
    b = budget_for_identity(ident, "第三轮逻辑审计 context_pipeline")
    assert b is not None and b >= 120_000


def test_is_budget_exceeded():
    assert is_budget_exceeded_result(
        "[Budget Exceeded] 进程 token 预算耗尽，运行已中断（已用 0/30000，拒绝 +33253）"
    )
    assert is_budget_exceeded_result("由于进程 token 预算不足，运行已中断。")
    assert not is_budget_exceeded_result("# 安全审计报告\n完成")


def test_ceo_payload_budget_overrides_auto():
    from backend.agent.workforce_budget import resolve_job_budget

    ident = SimpleNamespace(
        default_token_budget=100_000,
        role="engineer",
        name="工",
    )
    b, src = resolve_job_budget(
        ident,
        "系统健康检查 审计全仓",
        payload={"token_budget": 300_000, "budget_source": "ceo_assign"},
    )
    assert src == "ceo"
    assert b == 300_000
    b0, src0 = resolve_job_budget(ident, "说你好", payload={"token_budget": 0})
    assert src0 == "ceo" and b0 == 0
    b_auto, src_auto = resolve_job_budget(ident, "说你好", payload={})
    assert src_auto == "auto"
    assert b_auto == 100_000


def test_health_check_lifts_high():
    from backend.agent.workforce_budget import (
        budget_fail_system_summary,
        instruction_size_signals,
        split_hint_for_instruction,
    )

    b = suggested_token_budget(
        base=30_000,
        instruction=(
            "## Tevarn 系统体检\n### 任务1：后端\n### 任务2：前端\n### 任务3：kernel\n"
            + ("细节" * 400)
        ),
        role="qa-engineer",
    )
    assert b is not None and b >= 180_000
    sig = instruction_size_signals(
        "## 体检\n### 任务1\n### 任务2\n### 任务3\n" + ("x" * 2000)
    )
    assert sig["should_split"] is True
    assert split_hint_for_instruction("## 系统体检\n### 任务1\n### 任务2\n### 任务3\n" + ("y" * 2000))
    s = budget_fail_system_summary(
        instruction="全仓健康检查",
        raw="[Budget Exceeded] 已用 90000/100000",
        process_id="abc123",
    )
    assert "[Budget Exceeded]" in s
    assert "未完成实质检查" in s
    assert "报告框架" in s
