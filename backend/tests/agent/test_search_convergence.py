"""研究任务收敛刹车测试：重复搜索 + 全局预算 + 近似同义。"""
from __future__ import annotations

from backend.agent.loop import NexusAgentLoop


def _bare_loop() -> NexusAgentLoop:
    return object.__new__(NexusAgentLoop)


def test_first_call_passes_second_warns_third_blocks() -> None:
    loop = _bare_loop()
    args = {"query": "新能源 政策 2026"}
    assert loop._search_repeat_verdict("web_search", args) is None
    assert loop._search_repeat_verdict("web_search", args) == "warn"
    assert loop._search_repeat_verdict("web_search", args) == "block"
    assert loop._search_repeat_verdict("web_search", args) == "block"


def test_word_order_normalized() -> None:
    loop = _bare_loop()
    assert loop._search_repeat_verdict("web_search", {"query": "新能源 政策"}) is None
    assert loop._search_repeat_verdict("web_search", {"query": "政策 新能源"}) == "warn"


def test_different_query_not_blocked() -> None:
    loop = _bare_loop()
    assert loop._search_repeat_verdict("web_search", {"query": "新能源 政策"}) is None
    assert loop._search_repeat_verdict("web_search", {"query": "储能 市场 规模"}) is None


def test_non_search_tool_unaffected() -> None:
    loop = _bare_loop()
    for _ in range(5):
        assert loop._search_repeat_verdict("file_read", {"query": "/tmp/a.py"}) is None
        assert loop._search_repeat_verdict("terminal", {"command": "ls"}) is None


def test_case_insensitive() -> None:
    loop = _bare_loop()
    assert loop._search_repeat_verdict("web_search", {"query": "AI Agent"}) is None
    assert loop._search_repeat_verdict("web_search", {"query": "ai agent"}) == "warn"


def test_similar_query_jaccard_buckets() -> None:
    """高重叠 query 计入同一桶：第 2 次 warn，第 3 次 block。"""
    loop = _bare_loop()
    assert loop._search_repeat_verdict("web_search", {"query": "openai gpt5 release date news"}) is None
    assert loop._search_repeat_verdict("web_search", {"query": "openai gpt5 release date"}) == "warn"
    assert loop._search_repeat_verdict("web_search", {"query": "openai gpt5 release news date"}) == "block"


def test_global_search_budget_blocks() -> None:
    """不同 query 累计超过 max_per_run 后强制 block。"""
    loop = _bare_loop()
    import backend.core.config as cfg

    old = getattr(cfg.settings, "agent_search_max_per_run", 8)
    try:
        cfg.settings.agent_search_max_per_run = 3
        v1 = loop._search_repeat_verdict("web_search", {"query": "q1 alpha unique"})
        v2 = loop._search_repeat_verdict("web_search", {"query": "q2 beta unique"})
        v3 = loop._search_repeat_verdict("web_search", {"query": "q3 delta unique"})
        v4 = loop._search_repeat_verdict("web_search", {"query": "q4 zeta unique"})
        assert v1 is None
        # 接近预算时允许 warn
        assert v2 in (None, "warn")
        assert v3 in (None, "warn", "block")
        assert v4 == "block"
    finally:
        cfg.settings.agent_search_max_per_run = old
