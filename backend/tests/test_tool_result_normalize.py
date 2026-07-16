"""tool_result None/非字符串时不应再触发 len(None)。"""

from __future__ import annotations


def normalize_tool_result(tool_result, max_len: int = 12_000) -> str:
    """与 agent/loop.py 中截断前逻辑保持一致。"""
    if tool_result is None:
        tool_result = ""
    elif not isinstance(tool_result, str):
        tool_result = str(tool_result)
    if len(tool_result) > max_len:
        tool_result = (
            tool_result[:max_len] + f"\n\n[截断: 结果超过 {max_len} 字符]"
        )
    return tool_result


def test_none_becomes_empty_string():
    assert normalize_tool_result(None) == ""


def test_non_string_coerced():
    assert normalize_tool_result(42) == "42"
    assert normalize_tool_result({"a": 1}) == "{'a': 1}"


def test_truncate_long_string():
    raw = "x" * 15_000
    out = normalize_tool_result(raw, max_len=100)
    assert len(out) < 15_000
    assert out.startswith("x" * 100)
    assert "截断" in out


def test_short_string_unchanged():
    assert normalize_tool_result("hello") == "hello"
