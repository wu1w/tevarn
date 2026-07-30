# -*- coding: utf-8 -*-
"""cache_protocol unit tests."""
from __future__ import annotations

from backend.services.llm.cache_protocol import (
    apply_anthropic_style_cache,
    apply_openai_prompt_cache_key,
    mark_cache_breakpoint,
    strip_cache_control,
)


def test_mark_cache_breakpoint():
    blocks = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    mark_cache_breakpoint(blocks)
    assert blocks[-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[0]


def test_apply_anthropic_payload():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
        {"role": "user", "content": "more"},
    ]
    payload = {
        "system": "stable system",
        "tools": [{"name": "t1", "input_schema": {}}],
        "messages": messages,
    }
    apply_anthropic_style_cache(payload, messages, enabled=True, mark_tools=True)
    assert isinstance(payload["system"], list)
    assert payload["system"][0]["cache_control"]["type"] == "ephemeral"
    assert payload["tools"][-1]["cache_control"]["type"] == "ephemeral"
    # penultimate message marked
    assert isinstance(messages[-2]["content"], list)


def test_apply_openai_messages_no_tool_mark():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
    ]
    tools = [{"type": "function", "function": {"name": "x", "parameters": {}}}]
    payload = {"messages": messages, "tools": tools, "model": "m"}
    apply_anthropic_style_cache(
        payload, messages, enabled=True, mark_tools=False
    )
    assert "cache_control" not in tools[0]
    assert isinstance(messages[0]["content"], list)
    assert messages[0]["content"][0]["cache_control"]["type"] == "ephemeral"


def test_prompt_cache_key():
    p: dict = {}
    apply_openai_prompt_cache_key(p, cache_key="sess-abc", enabled=True)
    assert p["prompt_cache_key"] == "sess-abc"


def test_strip_cache_control():
    obj = {
        "a": [{"text": "x", "cache_control": {"type": "ephemeral"}}],
        "cache_control": {"type": "ephemeral"},
    }
    out = strip_cache_control(obj)
    assert "cache_control" not in out
    assert "cache_control" not in out["a"][0]
