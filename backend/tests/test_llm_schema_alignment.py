"""LLM provider schema / stream alignment (docs 2025-2026)."""
from __future__ import annotations


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
