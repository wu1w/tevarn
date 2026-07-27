"""研究任务收敛刹车测试（0.4.4 补丁）：重复搜索软干预。

诊断背景：system prompt 全文只有「别停」没有「何时停」，
loop 对重复搜索无感知，max_iterations 40 + 自动续段 ≈ 100 轮真上限，
导致研究类任务反复搜索二三十轮不总结。
"""

from __future__ import annotations

from backend.agent.loop import NexusAgentLoop


def _bare_loop() -> NexusAgentLoop:
    """绕过 __init__ 的裸实例——测的是纯函数式方法（不触 DB/LLM）。"""
    return object.__new__(NexusAgentLoop)


def test_first_call_passes_second_warns_third_blocks() -> None:
    loop = _bare_loop()
    args = {"query": "新能源 政策 2026"}
    assert loop._search_repeat_verdict("web_search", args) is None
    assert loop._search_repeat_verdict("web_search", args) == "warn"
    assert loop._search_repeat_verdict("web_search", args) == "block"
    assert loop._search_repeat_verdict("web_search", args) == "block"


def test_word_order_normalized() -> None:
    """词序变体视为同一查询（"A B" ≡ "B A"）。"""
    loop = _bare_loop()
    assert loop._search_repeat_verdict("web_search", {"query": "新能源 政策"}) is None
    assert loop._search_repeat_verdict("web_search", {"query": "政策 新能源"}) == "warn"


def test_different_query_not_blocked() -> None:
    """真正不同的查询互不影响。"""
    loop = _bare_loop()
    assert loop._search_repeat_verdict("web_search", {"query": "新能源 政策"}) is None
    assert loop._search_repeat_verdict("web_search", {"query": "储能 市场 规模"}) is None


def test_non_search_tool_unaffected() -> None:
    """非搜索工具的合法重复（重读文件等）不受干预。"""
    loop = _bare_loop()
    for _ in range(5):
        assert loop._search_repeat_verdict("file_read", {"query": "/tmp/a.py"}) is None
        assert loop._search_repeat_verdict("terminal", {"command": "ls"}) is None


def test_case_insensitive() -> None:
    loop = _bare_loop()
    assert loop._search_repeat_verdict("web_search", {"query": "AI Agent"}) is None
    assert loop._search_repeat_verdict("web_search", {"query": "ai agent"}) == "warn"
