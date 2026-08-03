# -*- coding: utf-8 -*-
"""Decisiveness gates: direct intent, thrash fingerprint, clarify strip."""
from __future__ import annotations

from types import SimpleNamespace

from backend.agent.decisive import (
    is_tool_thrash,
    tool_round_fingerprint,
)
from backend.agent.direct_intent import (
    filter_clarify_from_tools,
    is_direct_execute_intent,
    last_user_text,
)


def test_direct_execute_patterns():
    assert is_direct_execute_intent("你按照我的指令来，先清理分支")
    assert is_direct_execute_intent("直接执行，不要问了")
    assert is_direct_execute_intent("just do it, don't ask")
    assert not is_direct_execute_intent("帮我看看这个方案怎么样？")
    assert not is_direct_execute_intent("【系统·编制自动回调】工单结束")


def test_filter_clarify_from_tools():
    tools = [
        {"type": "function", "function": {"name": "grep"}},
        {"type": "function", "function": {"name": "clarify"}},
        {"type": "function", "function": {"name": "file_read"}},
    ]
    out = filter_clarify_from_tools(tools, user_text="按我说的直接做")
    names = [(t.get("function") or {}).get("name") for t in out]
    assert "clarify" not in names
    assert "grep" in names

    out2 = filter_clarify_from_tools(tools, user_text="你觉得该怎么设计？")
    assert any((t.get("function") or {}).get("name") == "clarify" for t in out2)


def test_last_user_text():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "直接执行合并"},
    ]
    assert "直接执行" in last_user_text(msgs)


def test_tool_round_fingerprint_stable():
    a = SimpleNamespace(name="grep", arguments={"pattern": "foo", "path": "x.py"})
    b = SimpleNamespace(name="grep", arguments={"path": "x.py", "pattern": "foo"})
    assert tool_round_fingerprint([a]) == tool_round_fingerprint([b])
    c = SimpleNamespace(name="grep", arguments={"pattern": "bar", "path": "x.py"})
    assert tool_round_fingerprint([a]) != tool_round_fingerprint([c])


def test_is_tool_thrash():
    assert is_tool_thrash("abc", "abc", thrash_streak=1, force_after=2)
    assert not is_tool_thrash("abc", "abc", thrash_streak=0, force_after=2)
    assert not is_tool_thrash("abc", "def", thrash_streak=5, force_after=2)
