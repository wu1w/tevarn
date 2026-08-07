"""Provider tool protocol: Anthropic pairing/is_error + OpenAI stream index merge."""

from __future__ import annotations

import json

import pytest

from backend.services.llm.anthropic import AnthropicService
from backend.services.llm.openai_compatible import merge_stream_tool_delta


@pytest.fixture
def anthropic_svc():
    class _Cfg:
        base_url = "https://api.anthropic.com"
        model = "claude-sonnet-4"
        max_tokens = 1024
        temperature = 0.2
        api_key = "sk-test"

    return AnthropicService(config=_Cfg())


def test_anthropic_tool_result_is_error(anthropic_svc):
    messages = [
        {"role": "user", "content": "read"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "toolu_abc",
                    "type": "function",
                    "function": {
                        "name": "file_read",
                        "arguments": json.dumps({"path": "x"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "toolu_abc",
            "content": "[Error] permission denied",
        },
    ]
    _sys, amsgs = anthropic_svc._convert_messages(messages)
    user_with_results = [m for m in amsgs if m["role"] == "user" and isinstance(m["content"], list)]
    assert user_with_results
    blocks = user_with_results[0]["content"]
    assert blocks[0]["type"] == "tool_result"
    assert blocks[0]["tool_use_id"] == "toolu_abc"
    assert blocks[0].get("is_error") is True


def test_anthropic_drops_orphan_tool_result(anthropic_svc):
    """Compressed history: tool row without matching assistant.tool_calls → drop."""
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "tool",
            "tool_call_id": "toolu_orphan",
            "content": "stale result from L3 compress",
        },
        {"role": "user", "content": "continue"},
    ]
    _sys, amsgs = anthropic_svc._convert_messages(messages)
    for m in amsgs:
        if m["role"] == "user" and isinstance(m.get("content"), list):
            for b in m["content"]:
                assert b.get("type") != "tool_result" or b.get("tool_use_id") != "toolu_orphan"


def test_anthropic_keeps_paired_tool_result(anthropic_svc):
    messages = [
        {"role": "user", "content": "x"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "toolu_ok",
                    "function": {"name": "web_search", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "toolu_ok", "content": "ok results"},
    ]
    _sys, amsgs = anthropic_svc._convert_messages(messages)
    asst = next(m for m in amsgs if m["role"] == "assistant")
    assert any(b.get("type") == "tool_use" and b.get("id") == "toolu_ok" for b in asst["content"])
    user = next(
        m
        for m in amsgs
        if m["role"] == "user" and isinstance(m.get("content"), list)
    )
    assert user["content"][0]["tool_use_id"] == "toolu_ok"
    assert "is_error" not in user["content"][0]


def test_anthropic_dangling_tool_use_gets_error_stub(anthropic_svc):
    """Compress left tool_calls but tool rows lost/wrong id → inject is_error results."""
    messages = [
        {"role": "user", "content": "search"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "toolu_a",
                    "function": {"name": "web_search", "arguments": "{}"},
                },
                {
                    "id": "toolu_b",
                    "function": {"name": "grep", "arguments": "{}"},
                },
            ],
        },
        # Wrong / empty ids — would be dropped as orphans
        {"role": "tool", "tool_call_id": "wrong-id", "content": "noise"},
        {"role": "tool", "tool_call_id": "", "content": "more noise"},
        {"role": "user", "content": "continue"},
    ]
    _sys, amsgs = anthropic_svc._convert_messages(messages)
    # Find user message that is pure tool_result list (injected stubs)
    stub_users = [
        m
        for m in amsgs
        if m["role"] == "user"
        and isinstance(m.get("content"), list)
        and m["content"]
        and all(b.get("type") == "tool_result" for b in m["content"])
    ]
    assert stub_users, "expected injected tool_result user turn"
    blocks = stub_users[0]["content"]
    ids = {b["tool_use_id"] for b in blocks}
    assert ids == {"toolu_a", "toolu_b"}
    for b in blocks:
        assert b.get("is_error") is True
        assert "compress" in b.get("content", "").lower() or "Error" in b.get(
            "content", ""
        )
    # Must not leave assistant tool_use without a following tool_result turn
    asst_idx = next(i for i, m in enumerate(amsgs) if m["role"] == "assistant")
    assert asst_idx + 1 < len(amsgs)
    nxt = amsgs[asst_idx + 1]
    assert nxt["role"] == "user"
    assert isinstance(nxt["content"], list)


def test_anthropic_partial_match_stubs_only_missing(anthropic_svc):
    messages = [
        {"role": "user", "content": "x"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "toolu_ok", "function": {"name": "a", "arguments": "{}"}},
                {"id": "toolu_miss", "function": {"name": "b", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "toolu_ok", "content": "real ok"},
        # toolu_miss never arrives
        {"role": "user", "content": "next"},
    ]
    _sys, amsgs = anthropic_svc._convert_messages(messages)
    result_turn = next(
        m
        for m in amsgs
        if m["role"] == "user"
        and isinstance(m.get("content"), list)
        and any(b.get("type") == "tool_result" for b in m["content"])
    )
    by_id = {b["tool_use_id"]: b for b in result_turn["content"]}
    assert by_id["toolu_ok"]["content"] == "real ok"
    assert "is_error" not in by_id["toolu_ok"]
    assert by_id["toolu_miss"].get("is_error") is True


def test_openai_stream_tool_merge_by_index_parallel():
    """Two parallel tools stream with interleaved index 0/1 deltas."""
    acc: dict[int, dict] = {}
    merge_stream_tool_delta(
        acc,
        {"index": 0, "id": "call_a", "function": {"name": "grep", "arguments": ""}},
    )
    merge_stream_tool_delta(
        acc,
        {"index": 1, "id": "call_b", "function": {"name": "glob", "arguments": ""}},
    )
    merge_stream_tool_delta(acc, {"index": 0, "function": {"arguments": '{"q":'}})
    merge_stream_tool_delta(acc, {"index": 1, "function": {"arguments": '{"g":'}})
    merge_stream_tool_delta(acc, {"index": 0, "function": {"arguments": '"x"}'}})
    merge_stream_tool_delta(acc, {"index": 1, "function": {"arguments": '"**/*"}'}})

    assert set(acc.keys()) == {0, 1}
    assert acc[0]["id"] == "call_a" and acc[0]["name"] == "grep"
    assert acc[1]["id"] == "call_b" and acc[1]["name"] == "glob"
    assert json.loads(acc[0]["arguments"]) == {"q": "x"}
    assert json.loads(acc[1]["arguments"]) == {"g": "**/*"}
