"""T4：Anthropic prompt caching 的 payload 契约 + 用量解析。

Agent loop 每轮重发整个 messages，system + 工具 schema + 全部历史被反复计费。
本文件锁定断点位置与用量口径；没有 cache_read_input_tokens 就无法验证缓存生效。
"""

import pytest

from backend.services.llm.anthropic import (
    AnthropicService,
    _extract_usage,
)


@pytest.fixture
def svc(monkeypatch):
    class _Cfg:
        base_url = "https://api.anthropic.com"
        model = "claude-opus-5"
        max_tokens = 4096
        temperature = 1.0
        api_key = "sk-test"

    return AnthropicService(config=_Cfg())


def _payload(svc, messages, tools=None):
    system_text, amsgs = svc._convert_messages(messages)
    payload = {"model": svc.model, "messages": amsgs}
    if system_text:
        payload["system"] = system_text
    if tools:
        payload["tools"] = svc._convert_tools(tools)
    svc._apply_prompt_cache(payload, amsgs)
    return payload


TOOLS = [
    {"function": {"name": "file_read", "description": "read", "parameters": {}}},
    {"function": {"name": "grep", "description": "search", "parameters": {}}},
]


def test_system_block_gets_cache_breakpoint(svc):
    p = _payload(
        svc,
        [{"role": "system", "content": "stable prompt"}, {"role": "user", "content": "hi"}],
    )
    assert isinstance(p["system"], list)
    assert p["system"][0]["text"] == "stable prompt"
    assert p["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_tools_get_trailing_breakpoint_only(svc):
    p = _payload(svc, [{"role": "user", "content": "hi"}], tools=TOOLS)
    # 缓存的是断点之前的整个前缀，故只标最后一个工具
    assert "cache_control" not in p["tools"][0]
    assert p["tools"][-1]["cache_control"] == {"type": "ephemeral"}


def test_history_breakpoint_leaves_latest_turn_uncached(svc):
    """倒数第二条打断点：最新一轮不缓存，下一轮才能增量命中。"""
    p = _payload(
        svc,
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "turn one"},
            {"role": "assistant", "content": "answer one"},
            {"role": "user", "content": "turn two"},
        ],
    )
    msgs = p["messages"]
    assert isinstance(msgs[-2]["content"], list)
    assert msgs[-2]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    # 最后一条保持原样（未缓存）
    assert msgs[-1]["content"] == "turn two"


def test_cache_can_be_disabled(svc, monkeypatch):
    monkeypatch.setattr(
        "backend.services.llm.anthropic.settings.agent_prompt_cache_anthropic",
        False,
        raising=False,
    )
    p = _payload(
        svc,
        [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        tools=TOOLS,
    )
    assert isinstance(p["system"], str)
    assert all("cache_control" not in t for t in p["tools"])


def test_single_message_has_no_history_breakpoint(svc):
    """只有一条消息时不该越界去标断点。"""
    p = _payload(svc, [{"role": "user", "content": "hi"}])
    assert p["messages"][0]["content"] == "hi"


def test_empty_system_not_promoted_to_block(svc):
    p = _payload(svc, [{"role": "user", "content": "hi"}])
    assert "system" not in p


# ── 用量口径 ────────────────────────────────────────────────


def test_usage_sums_cached_and_fresh_input():
    """Anthropic 的 input_tokens 不含缓存命中部分，须相加才与 OpenAI 口径一致。"""
    u = _extract_usage(
        {
            "input_tokens": 100,
            "cache_read_input_tokens": 900,
            "cache_creation_input_tokens": 50,
            "output_tokens": 30,
        }
    )
    assert u["prompt_tokens"] == 1050
    assert u["cache_read_input_tokens"] == 900
    assert u["cache_creation_input_tokens"] == 50
    assert u["completion_tokens"] == 30


def test_usage_tolerates_missing_and_garbage_fields():
    assert _extract_usage({}) == {}
    u = _extract_usage({"input_tokens": "x", "output_tokens": None})
    assert u == {}
    u2 = _extract_usage({"input_tokens": -5, "output_tokens": 7})
    assert u2.get("completion_tokens") == 7


def test_usage_without_cache_still_reports_prompt_tokens():
    u = _extract_usage({"input_tokens": 500, "output_tokens": 20})
    assert u["prompt_tokens"] == 500
    assert u["cache_read_input_tokens"] == 0
