# -*- coding: utf-8 -*-
"""ProviderProfile registry + resolution tests."""
from __future__ import annotations

from backend.services.llm.provider_profiles import (
    explicit_cache_enabled,
    profile_as_dict,
    resolve_profile,
)


def test_resolve_by_provider_id():
    assert resolve_profile(provider_id="deepseek").family == "deepseek"
    assert resolve_profile(provider_id="qwen").family == "qwen"
    assert resolve_profile(provider_id="zhipu").family == "glm"
    assert resolve_profile(provider_id="minimax").family == "minimax"
    assert resolve_profile(provider_id="minimax-cn").family == "minimax"
    assert resolve_profile(provider_id="mimo").family == "mimo"
    assert resolve_profile(provider_id="kimi-plan").family == "kimi"
    assert resolve_profile(provider_id="xai").family == "xai"
    assert resolve_profile(provider_id="xai-oauth").family == "xai"
    assert resolve_profile(provider_id="anthropic").family == "anthropic"
    assert resolve_profile(provider_id="openai").family == "openai"


def test_resolve_by_base_url():
    assert resolve_profile(base_url="https://api.deepseek.com").family == "deepseek"
    assert resolve_profile(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
    ).family == "qwen"
    assert resolve_profile(base_url="https://open.bigmodel.cn/api/paas/v4").family == "glm"
    assert resolve_profile(base_url="https://api.minimax.io/v1").family == "minimax"
    assert resolve_profile(base_url="https://api.xiaomimimo.com/v1").family == "mimo"
    assert resolve_profile(base_url="https://api.x.ai/v1").family == "xai"
    assert resolve_profile(base_url="https://api.kimi.com/coding/v1").family == "kimi"


def test_resolve_by_model():
    assert resolve_profile(model="deepseek-chat").family == "deepseek"
    assert resolve_profile(model="qwen-plus").family == "qwen"
    assert resolve_profile(model="glm-4-flash").family == "glm"
    assert resolve_profile(model="MiniMax-M2.5").family == "minimax"
    assert resolve_profile(model="mimo-v2.5-pro").family == "mimo"
    assert resolve_profile(model="grok-4").family == "xai"
    assert resolve_profile(model="claude-sonnet-4").family == "anthropic"


def test_openrouter_nested_model():
    p = resolve_profile(provider_id="openrouter", model="deepseek/deepseek-chat")
    assert p.family == "openrouter"
    # nested deepseek profile traits preserved
    assert p.cache_mode in ("implicit", "both", "none", "explicit_anthropic")


def test_kimi_force_temperature():
    p = resolve_profile(provider_id="kimi-plan")
    assert p.force_temperature == 1.0


def test_mimo_large_window():
    p = resolve_profile(provider_id="mimo")
    assert p.default_context_window >= 256_000
    assert p.prefer_billable_tokens is True


def test_minimax_tools_first():
    p = resolve_profile(provider_id="minimax")
    assert p.tools_before_system is True


def test_reasoning_detection():
    p = resolve_profile(provider_id="deepseek")
    assert p.is_reasoning_model("deepseek-reasoner") is True
    assert p.is_reasoning_model("deepseek-chat") is False


def test_profile_as_dict():
    d = profile_as_dict(resolve_profile(provider_id="qwen"))
    assert d["family"] == "qwen"
    assert "cache_mode" in d


def test_explicit_cache_flags_default_off_for_qwen(monkeypatch):
    p = resolve_profile(provider_id="qwen")
    # default settings: qwen explicit off
    assert explicit_cache_enabled(p) is False
    monkeypatch.setattr(
        "backend.core.config.settings.agent_prompt_cache_qwen_explicit", True, raising=False
    )
    assert explicit_cache_enabled(p) is True
