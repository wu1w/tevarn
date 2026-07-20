# -*- coding: utf-8 -*-
"""Provider 构造时 max_tokens / temperature 钳制的集成验证。

直接实例化各 provider，传入用户可能瞎填的非法值，
验证 self.max_tokens / self.temperature 被钳到安全区间。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.services.llm.anthropic import AnthropicService
from backend.services.llm.ollama import OllamaService
from backend.services.llm.openai_compatible import OpenAICompatibleService
from backend.services.llm.vllm import VLLMService


def _cfg(model="test-model", max_tokens=None, temperature=None):
    return SimpleNamespace(
        base_url="http://localhost:1",
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        api_key=None,
    )


@pytest.mark.parametrize("cls", [AnthropicService, OllamaService, OpenAICompatibleService, VLLMService])
@pytest.mark.parametrize("bad", [0, -100, "garbage", None, 999_999_999])
def test_provider_max_tokens_never_invalid(cls, bad):
    svc = cls(_cfg(max_tokens=bad, temperature=0.7))
    assert isinstance(svc.max_tokens, int)
    assert svc.max_tokens >= 1
    assert svc.max_tokens <= 1_000_000


@pytest.mark.parametrize("cls", [AnthropicService, OllamaService, OpenAICompatibleService, VLLMService])
@pytest.mark.parametrize("bad", [-5, 99, "x", None, float("nan")])
def test_provider_temperature_never_invalid(cls, bad):
    svc = cls(_cfg(max_tokens=4096, temperature=bad))
    assert isinstance(svc.temperature, float)
    assert 0.0 <= svc.temperature <= 2.0


def test_anthropic_hard_output_cap_enforced():
    # claude-3-sonnet 硬输出上限 4096
    svc = AnthropicService(_cfg(model="claude-3-sonnet-20240229", max_tokens=100_000))
    assert svc.max_tokens == 4096


def test_factory_snapshot_garbage_max_tokens():
    # snapshot 传垃圾 max_tokens 不应让 factory 崩，且被钳制
    from backend.services.llm.factory import LLMServiceFactory
    snap = {
        "provider": "openai-compatible",
        "base_url": "http://localhost:1",
        "model": "test-model",
        "max_tokens": "not-a-number",
        "temperature": "also-garbage",
    }
    svc = LLMServiceFactory.get_service_for_snapshot(snap)
    assert isinstance(svc.max_tokens, int) and svc.max_tokens >= 1
    assert 0.0 <= svc.temperature <= 2.0
