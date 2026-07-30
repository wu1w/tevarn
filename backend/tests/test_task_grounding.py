"""Multi-category task grounding (soft-default policy)."""

from __future__ import annotations

import pytest

from backend.agent.completion_gate import evaluate_completion
from backend.agent.grounding_policy import clear_policy_cache
from backend.agent.task_grounding import (
    annotate_grounded_report,
    classify_all,
    classify_task,
    is_audit_like_task,
    maybe_annotate_report,
)


@pytest.fixture(autouse=True)
def _soft_mode(monkeypatch):
    monkeypatch.setenv("TAKTON_GROUNDING_MODE", "soft")
    clear_policy_cache()
    yield
    clear_policy_cache()


def test_classify_families():
    assert classify_task("请做安全审计") == "audit"
    assert classify_task("搜索最新 AI 新闻") == "research"
    assert classify_task("统计一下占比和均值") == "data_stats"
    assert classify_task("用 python 算一下 17*19") == "math"
    assert classify_task("根据 README 总结安装步骤") == "doc_qa"
    assert classify_task("列出当前有哪些员工") == "inventory"
    assert classify_task("为什么服务 503 了排查一下") == "diagnose"
    assert classify_task("对比 Redis 和 Memcached") == "compare"
    assert classify_task("这个说法有出处吗事实核查") == "cite_fact"
    assert classify_task("修这个 bug") == "fix"
    assert is_audit_like_task("代码审查")


def test_research_gate():
    v = evaluate_completion("查一下今天汇率最新", [], "大概 7.2")
    assert not v.ok
    assert "research" in v.reason or "no_tools" in v.reason
    v2 = evaluate_completion(
        "调研最新模型发布",
        ["web_search", "web_fetch"],
        "据搜索，见 https://example.com/a",
    )
    assert v2.ok


def test_data_stats_gate():
    v = evaluate_completion("统计订单占比", [], "转化率约 37%")
    assert not v.ok
    v2 = evaluate_completion(
        "计算 CSV 平均客单价",
        ["file_read", "python"],
        "均值 128.5，n=40",
    )
    assert v2.ok


def test_math_gate():
    v = evaluate_completion("计算 123*456", [], "56088")
    assert not v.ok
    v2 = evaluate_completion("算一下 2**10", ["python"], "1024")
    assert v2.ok


def test_doc_qa_gate():
    v = evaluate_completion("根据手册总结配置步骤", [], "第一步 balabala")
    assert not v.ok


def test_inventory_gate():
    v = evaluate_completion("列出有哪些进程在跑", [], "大概三五个")
    assert not v.ok


def test_diagnose_gate():
    v = evaluate_completion("为什么接口超时了", [], "肯定是网络问题")
    assert not v.ok


def test_audit_still_works():
    v = evaluate_completion("请审计 backend", [], "High everywhere")
    assert not v.ok
    assert v.reason == "audit_no_tools"
    v2 = evaluate_completion(
        "审计权限",
        ["glob", "grep", "file_read", "file_read"],
        "见 identity.py",
    )
    assert v2.ok


def test_soft_allows_shallow_with_some_tools():
    """Once any real tool ran, soft mode does not force more rituals."""
    v = evaluate_completion(
        "审计权限",
        ["grep"],
        "见 identity 相关模块，建议继续深挖",
    )
    assert v.ok


def test_fix_still_requires_write():
    v = evaluate_completion("修 bug", ["glob", "grep"], "修好了")
    assert not v.ok
    assert "write" in v.reason or "fix" in v.reason


def test_footer_research_no_web():
    out = annotate_grounded_report(
        "最新消息表明市场涨了 12%。",
        user_input="搜索最新市场消息",
        tools_used=[],
    )
    assert "落地校验" in out
    assert "web_search" in out or "检索" in out


def test_footer_stats_no_calc():
    out = annotate_grounded_report(
        "转化率约 42%，平均客单 99。",
        user_input="统计一下转化率",
        tools_used=["file_read"],
    )
    assert "落地校验" in out


def test_maybe_skips_chitchat():
    t = "好的，已记下。"
    assert maybe_annotate_report("嗯", t, []) == t


def test_classify_all_multi():
    # first match is primary; all may include more if overlapping
    kids = classify_all("审计并统计漏洞占比")
    assert "audit" in kids
