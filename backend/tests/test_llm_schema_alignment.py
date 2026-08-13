"""LLM provider schema / stream alignment (docs 2025-2026)."""
from __future__ import annotations

import json
import uuid


def test_merge_stream_preserves_thought_signature():
    from backend.services.llm.openai_compatible import merge_stream_tool_delta

    acc: dict = {}
    merge_stream_tool_delta(
        acc,
        {
            "index": 0,
            "id": "call_1",
            "function": {"name": "get_weather", "arguments": "{\"c"},
            "extra_content": {"google": {"thought_signature": "SIG123"}},
        },
    )
    merge_stream_tool_delta(
        acc,
        {
            "index": 0,
            "function": {"arguments": "ity\":\"SF\"}"},
        },
    )
    assert acc[0]["name"] == "get_weather"
    assert "SF" in acc[0]["arguments"]
    assert acc[0]["extra_content"]["google"]["thought_signature"] == "SIG123"


def test_tool_call_from_openai_keeps_signature():
    from backend.services.llm.openai_compatible import _tool_call_from_openai

    tc = _tool_call_from_openai(
        {
            "id": "x",
            "function": {"name": "f", "arguments": "{}"},
            "extra_content": {"google": {"thought_signature": "abc"}},
        }
    )
    assert tc.name == "f"
    assert tc.thought_signature == "abc"
    assert tc.extra_content is not None


def test_tool_call_emit_roundtrip_keeps_signature():
    """Gemini 3: next-turn assistant.tool_calls must echo extra_content."""
    from backend.services.llm.openai_compatible import (
        _tool_call_from_openai,
        tool_call_to_openai_message,
    )

    tc = _tool_call_from_openai(
        {
            "id": "call_g",
            "function": {"name": "file_read", "arguments": '{"path":"a"}'},
            "extra_content": {"google": {"thought_signature": "SIG-KEEP"}},
        }
    )
    shaped = tool_call_to_openai_message(tc)
    assert shaped["extra_content"]["google"]["thought_signature"] == "SIG-KEEP"
    assert shaped["thought_signature"] == "SIG-KEEP"
    assert shaped["function"]["name"] == "file_read"
    roundtrip = _tool_call_from_openai(shaped)
    assert roundtrip.thought_signature == "SIG-KEEP"


def test_stream_usage_after_finish_reason_is_kept():
    """include_usage sends usage in a later empty-choices chunk — do not stop early."""
    from backend.services.llm.openai_compatible import OpenAIStreamAccumulator

    acc = OpenAIStreamAccumulator(uuid.uuid4(), normalize_usage=lambda u: {
        "prompt_tokens": int(u.get("prompt_tokens") or 0),
        "completion_tokens": int(u.get("completion_tokens") or 0),
    })
    done, chunks = acc.consume_data_line(json.dumps({
        "choices": [{
            "delta": {"content": "hi"},
            "finish_reason": "stop",
        }],
    }))
    assert done is False
    assert any(c.delta == "hi" for c in chunks)
    assert all(c.finish_reason is None for c in chunks)

    done, chunks = acc.consume_data_line(json.dumps({
        "choices": [],
        "usage": {"prompt_tokens": 11, "completion_tokens": 2},
    }))
    assert done is False
    assert chunks == []

    done, chunks = acc.consume_data_line("[DONE]")
    assert done is True
    finishes = [c for c in chunks if c.finish_reason]
    assert len(finishes) == 1
    assert finishes[0].finish_reason == "stop"
    assert finishes[0].usage.get("prompt_tokens") == 11
    assert finishes[0].usage.get("completion_tokens") == 2


def test_sse_split_handles_tcp_fragment_and_multi_event():
    from backend.services.llm.sse import split_sse_data_lines

    residual, payloads = split_sse_data_lines(
        'data: {"type":"a"}\n\ndata: {"type":"b"}\ndata: {"typ'
    )
    assert payloads == ['{"type":"a"}', '{"type":"b"}']
    assert residual == 'data: {"typ'
    residual2, payloads2 = split_sse_data_lines(residual + 'e":"c"}\n')
    assert payloads2 == ['{"type":"c"}']
    assert residual2 == ""


def test_gemini_profile_exists():
    from backend.services.llm.provider_profiles import _PROFILES

    assert "gemini" in _PROFILES
    assert _PROFILES["gemini"].stream_include_usage is True


def test_openai_stream_options_hook():
    from backend.services.llm.openai_compatible import OpenAICompatibleService
    from backend.services.llm.provider_profiles import _PROFILES

    class _Cfg:
        base_url = "https://api.openai.com/v1"
        model = "gpt-4o-mini"
        max_tokens = 128
        temperature = 0.2
        api_key = "x"

    svc = OpenAICompatibleService(config=_Cfg(), profile=_PROFILES["openai"])
    payload = {"model": "gpt-4o-mini", "messages": [], "stream": True}
    svc._apply_profile_payload_hooks(payload, [])
    assert payload.get("stream_options", {}).get("include_usage") is True


def test_anthropic_headers_include_tool_streaming_beta():
    from backend.services.llm.anthropic import AnthropicService

    class _Cfg:
        base_url = "https://api.anthropic.com"
        model = "claude-sonnet-4-5"
        max_tokens = 128
        temperature = 0.2
        api_key = "x"

    svc = AnthropicService(config=_Cfg())
    h = svc._get_headers()
    beta = h.get("anthropic-beta") or ""
    assert "fine-grained-tool-streaming-2025-05-14" in beta or "prompt-caching" in beta


def test_mcp_dirty_generation_not_one_shot_consume():
    from backend.mcp_hub import service as mcp_svc

    g0 = mcp_svc.tools_dirty_generation()
    mcp_svc.mark_tools_dirty()
    mcp_svc.mark_tools_dirty()
    g1 = mcp_svc.tools_dirty_generation()
    assert g1 >= g0 + 2
    # Concurrent sessions must still observe the bump after one consume
    mcp_svc.consume_tools_dirty()
    assert mcp_svc.tools_dirty_generation() == g1


def test_registry_executor_has_no_get_openai_tools():
    """Loop must not call get_openai_tools — that method does not exist."""
    from backend.integrations.registry_tool_executor import RegistryToolExecutor

    assert hasattr(RegistryToolExecutor, "list_schemas")
    assert not hasattr(RegistryToolExecutor, "get_openai_tools")
