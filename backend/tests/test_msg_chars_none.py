"""assistant content=None 时不能对 m.get('content') 做 len()。

复现：tool_calls 回合会把 assistant content 设为 None，
下一轮日志 sum(len(m.get('content',''))) 会 TypeError。
"""

from __future__ import annotations

import json
from typing import Any


def msg_chars(m: dict[str, Any]) -> int:
    """与 agent/loop.py 中 _msg_chars 保持一致。"""
    c = m.get("content")
    if c is None:
        return 0
    if isinstance(c, str):
        return len(c)
    if isinstance(c, list):
        try:
            return len(json.dumps(c, ensure_ascii=False))
        except Exception:
            return 0
    return len(str(c))


def test_none_content_does_not_raise():
    messages = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "content": "ok"},
        {"role": "user", "content": "hi"},
    ]
    total = sum(msg_chars(m) for m in messages)
    assert total == 4


def test_legacy_get_default_still_none():
    """证明 dict.get 默认值挡不住显式 None。"""
    m = {"content": None}
    assert m.get("content", "") is None


def test_legacy_len_raises():
    messages = [{"content": None}, {"content": "x"}]
    raised = False
    try:
        sum(len(m.get("content", "")) for m in messages)
    except TypeError:
        raised = True
    assert raised


def test_list_content_counts_json_len():
    m = {"content": [{"type": "text", "text": "ab"}]}
    n = msg_chars(m)
    assert n > 0
    assert n == len(json.dumps(m["content"], ensure_ascii=False))
