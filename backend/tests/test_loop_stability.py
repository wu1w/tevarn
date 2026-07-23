"""Loop 稳态：空回复判定、工具重复熔断、错误分级。"""
from __future__ import annotations

from backend.agent.robust import (
    ToolRepeatGuard,
    classify_tool_result_error,
    is_empty_assistant_content,
    tool_call_signature,
)


def test_is_empty_assistant_content():
    assert is_empty_assistant_content(None) is True
    assert is_empty_assistant_content("") is True
    assert is_empty_assistant_content("   \n\t") is True
    assert is_empty_assistant_content("ok") is False


def test_tool_call_signature_stable():
    a = tool_call_signature("search", {"q": "x", "n": 1})
    b = tool_call_signature("search", {"n": 1, "q": "x"})
    c = tool_call_signature("search", {"q": "y", "n": 1})
    assert a == b
    assert a != c
    assert tool_call_signature("search", '{"q":"x"}')  # string args ok


def test_tool_repeat_guard_trips_on_same_sig():
    g = ToolRepeatGuard(max_repeat=3)
    sig = tool_call_signature("bash", {"command": "ls"})
    assert g.observe([sig]) is False
    assert g.observe([sig]) is False
    assert g.observe([sig]) is True
    assert g.tripped is True
    # already tripped
    assert g.observe([sig]) is False


def test_tool_repeat_guard_resets_on_different():
    g = ToolRepeatGuard(max_repeat=3)
    s1 = tool_call_signature("a", {"x": 1})
    s2 = tool_call_signature("b", {"x": 1})
    assert g.observe([s1]) is False
    assert g.observe([s1]) is False
    assert g.observe([s2]) is False  # streak reset
    assert g.observe([s2]) is False
    assert g.tripped is False


def test_classify_tool_result_error():
    assert classify_tool_result_error("hello") is None
    assert classify_tool_result_error("[Error] Tool x timed out after 30s") == "transient"
    assert classify_tool_result_error("[Error] 工具 foo 执行失败（ValueError）。") == "fatal"
