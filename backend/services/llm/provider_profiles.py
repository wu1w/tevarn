"""Per-provider LLM profiles: cache mode, limits, TokenMeter coeffs, pipeline knobs.

Resolved from provider_id / base_url / model so openai-compatible backends
(DeepSeek, Qwen, GLM, MiniMax, MiMo, Kimi, xAI, …) get family-specific behaviour
without a separate service class each.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class ProviderProfile:
    family: str
    protocol: str  # anthropic | openai-compatible | ollama | vllm
    cache_mode: str  # none | implicit | explicit_anthropic | both
    cache_min_tokens: int = 1024
    cache_max_breakpoints: int = 4
    force_temperature: float | None = None
    merge_system_messages: bool = True
    allow_orphan_tool_drop: bool = True
    max_tool_arg_chars: int = 6000
    max_tool_result_chars: int = 12000
    default_context_window: int = 128_000
    chars_per_token_latin: float = 4.0
    chars_per_token_cjk: float = 1.5
    prefer_billable_tokens: bool = True
    reasoning_model_patterns: tuple[str, ...] = (
        r"reasoner",
        r"[-_/]r1\b",
        r"thinking",
        r"o1\b",
        r"o3\b",
        r"o4-mini",
    )
    l1_tool_chars: int = 12_000
    l3_threshold_ratio: float = 0.72
    l5_enabled_default: bool = True
    tools_before_system: bool = False  # MiniMax prefers tools → system → messages
    stream_include_usage: bool = True
    openai_prompt_cache_key: bool = False
    recommended_compress_model_hint: str = ""  # empty = use main non-reasoning

    def is_reasoning_model(self, model: str | None) -> bool:
        name = (model or "").strip()
        if not name:
            return False
        for pat in self.reasoning_model_patterns:
            if re.search(pat, name, re.I):
                return True
        return False


# ── Family defaults ──────────────────────────────────────────────────

_PROFILES: dict[str, ProviderProfile] = {
    "anthropic": ProviderProfile(
        family="anthropic",
        protocol="anthropic",
        cache_mode="explicit_anthropic",
        merge_system_messages=False,
        default_context_window=200_000,
        chars_per_token_latin=3.5,
        chars_per_token_cjk=1.4,
        l1_tool_chars=12_000,
        stream_include_usage=False,
        recommended_compress_model_hint="claude-haiku",
    ),
    "openai": ProviderProfile(
        family="openai",
        protocol="openai-compatible",
        cache_mode="implicit",
        merge_system_messages=False,
        default_context_window=128_000,
        openai_prompt_cache_key=True,
        stream_include_usage=True,
        recommended_compress_model_hint="gpt-4o-mini",
        reasoning_model_patterns=(
            r"\bo1\b",
            r"\bo3\b",
            r"\bo4-mini\b",
            r"reasoner",
            r"thinking",
        ),
    ),
    "xai": ProviderProfile(
        family="xai",
        protocol="openai-compatible",
        cache_mode="implicit",
        merge_system_messages=False,
        default_context_window=131_072,
        stream_include_usage=True,
        recommended_compress_model_hint="grok-3-mini",
    ),
    "kimi": ProviderProfile(
        family="kimi",
        protocol="openai-compatible",
        cache_mode="implicit",
        force_temperature=1.0,
        merge_system_messages=False,
        max_tool_arg_chars=5000,
        max_tool_result_chars=10_000,
        l1_tool_chars=8_000,
        default_context_window=128_000,
        chars_per_token_cjk=1.3,
        stream_include_usage=True,
    ),
    "moonshot": ProviderProfile(
        family="moonshot",
        protocol="openai-compatible",
        cache_mode="implicit",
        merge_system_messages=False,
        l1_tool_chars=10_000,
        default_context_window=128_000,
        chars_per_token_cjk=1.3,
        stream_include_usage=True,
    ),
    "deepseek": ProviderProfile(
        family="deepseek",
        protocol="openai-compatible",
        cache_mode="implicit",
        merge_system_messages=False,
        default_context_window=128_000,
        chars_per_token_cjk=1.4,
        prefer_billable_tokens=True,
        stream_include_usage=True,
        recommended_compress_model_hint="deepseek-chat",
        reasoning_model_patterns=(
            r"reasoner",
            r"[-_/]r1\b",
            r"thinking",
            r"deepseek-r1",
            r"deepseek-v4",
            r"v4-flash",
            r"v4-pro",
        ),
    ),
    "opencode": ProviderProfile(
        family="opencode",
        protocol="openai-compatible",
        cache_mode="implicit",
        merge_system_messages=False,
        default_context_window=128_000,
        stream_include_usage=True,
        prefer_billable_tokens=True,
    ),

    "qwen": ProviderProfile(
        family="qwen",
        protocol="openai-compatible",
        cache_mode="both",  # implicit default; explicit via settings
        cache_min_tokens=1024,
        cache_max_breakpoints=4,
        merge_system_messages=False,
        default_context_window=128_000,
        chars_per_token_cjk=1.35,
        stream_include_usage=True,
        recommended_compress_model_hint="qwen-turbo",
        reasoning_model_patterns=(r"thinking", r"qwq", r"reasoner"),
    ),
    "glm": ProviderProfile(
        family="glm",
        protocol="openai-compatible",
        cache_mode="implicit",
        merge_system_messages=False,
        default_context_window=128_000,
        chars_per_token_cjk=1.35,
        stream_include_usage=True,
        recommended_compress_model_hint="glm-4-flash",
    ),
    "minimax": ProviderProfile(
        family="minimax",
        protocol="openai-compatible",
        cache_mode="both",
        cache_min_tokens=512,
        merge_system_messages=False,
        tools_before_system=True,
        default_context_window=200_000,
        chars_per_token_cjk=1.4,
        stream_include_usage=True,
        recommended_compress_model_hint="MiniMax-M2.5",
    ),
    "mimo": ProviderProfile(
        family="mimo",
        protocol="openai-compatible",
        cache_mode="implicit",
        merge_system_messages=False,
        default_context_window=1_000_000,
        l1_tool_chars=16_000,
        l3_threshold_ratio=0.85,
        chars_per_token_cjk=1.35,
        prefer_billable_tokens=True,
        stream_include_usage=True,
        recommended_compress_model_hint="mimo-v2.5",
    ),
    "openrouter": ProviderProfile(
        family="openrouter",
        protocol="openai-compatible",
        cache_mode="implicit",
        merge_system_messages=False,
        default_context_window=128_000,
        stream_include_usage=True,
    ),
    "volcengine": ProviderProfile(
        family="volcengine",
        protocol="openai-compatible",
        cache_mode="implicit",
        merge_system_messages=False,
        default_context_window=32_000,
        stream_include_usage=True,
    ),
    "xfyun": ProviderProfile(
        family="xfyun",
        protocol="openai-compatible",
        cache_mode="implicit",
        merge_system_messages=True,
        max_tool_arg_chars=4000,
        max_tool_result_chars=8000,
        l1_tool_chars=8000,
        default_context_window=128_000,
        stream_include_usage=True,
    ),
    "ollama": ProviderProfile(
        family="ollama",
        protocol="ollama",
        cache_mode="none",
        merge_system_messages=True,
        prefer_billable_tokens=False,
        stream_include_usage=False,
        default_context_window=32_768,
    ),
    "vllm": ProviderProfile(
        family="vllm",
        protocol="vllm",
        cache_mode="implicit",  # local prefix cache
        merge_system_messages=True,
        prefer_billable_tokens=False,
        stream_include_usage=True,
        default_context_window=32_768,
    ),
    "generic": ProviderProfile(
        family="generic",
        protocol="openai-compatible",
        cache_mode="implicit",
        merge_system_messages=True,
        max_tool_result_chars=10_000,
        l1_tool_chars=10_000,
        default_context_window=128_000,
        stream_include_usage=True,
    ),
}


_PROVIDER_ID_MAP: dict[str, str] = {
    "anthropic": "anthropic",
    "claude": "anthropic",
    "openai": "openai",
    "xai": "xai",
    "xai-oauth": "xai",
    "kimi-plan": "kimi",
    "kimi": "kimi",
    "moonshot": "moonshot",
    "moonshot-intl": "moonshot",
    "deepseek": "deepseek",
    "qwen": "qwen",
    "zhipu": "glm",
    "glm": "glm",
    "minimax": "minimax",
    "minimax-cn": "minimax",
    "mimo": "mimo",
    "openrouter": "openrouter",
    "volcengine-ark": "volcengine",
    "volcengine": "volcengine",
    "xfyun-astron": "xfyun",
    "xfyun": "xfyun",
    "ollama": "ollama",
    "vllm": "vllm",
    # OpenCode 网关：用量上单独显示；底层协议仍 openai-compatible
    "opencode-zen": "opencode",
    "opencode-go": "opencode",
    "opencode": "opencode",
    # ChatGPT 会员 OAuth / Codex 订阅路径
    "openai-chatgpt-oauth": "openai",
    "openai-codex-oauth": "openai",
    "openai-chatgpt": "openai",
    "custom": "generic",
}


def _host(base_url: str) -> str:
    b = (base_url or "").strip()
    if not b:
        return ""
    try:
        return (urlparse(b if "://" in b else f"https://{b}").hostname or "").lower()
    except Exception:
        return b.lower()


def _family_from_url(base_url: str) -> str | None:
    h = _host(base_url)
    b = (base_url or "").lower()
    if not h and not b:
        return None
    if "anthropic" in h or "anthropic" in b:
        return "anthropic"
    if "api.openai.com" in h or h == "openai.com":
        return "openai"
    # 本机 Codex / ChatGPT OAuth 代理
    if "openai-codex" in b or "llm-proxy/openai" in b or "chatgpt.com" in b:
        return "openai"
    if "opencode.ai" in h or "opencode.ai" in b or "/zen/" in b or "/go/v1" in b:
        return "opencode"
    if "api.x.ai" in h or h.endswith(".x.ai") or "x.ai" in h:
        return "xai"
    if "kimi.com" in h or "kimi.com" in b:
        return "kimi"
    if "moonshot" in h:
        return "moonshot"
    if "deepseek" in h:
        return "deepseek"
    if "dashscope" in h or "aliyun" in h:
        return "qwen"
    if "bigmodel" in h or "z.ai" in h or "zhipu" in h:
        return "glm"
    if "minimax" in h or "minimaxi" in h:
        return "minimax"
    if "xiaomimimo" in h or "mimo.mi" in h or "xiaomimimo" in b:
        return "mimo"
    if "openrouter" in h:
        return "openrouter"
    if "volces.com" in h or "volcengine" in h:
        return "volcengine"
    if "xf-yun" in h or "xfyun" in h or "maas-" in h:
        return "xfyun"
    if "11434" in b or "ollama" in h:
        return "ollama"
    return None


def _family_from_model(model: str) -> str | None:
    m = (model or "").lower()
    if not m:
        return None
    # openrouter style org/model
    if "/" in m:
        org = m.split("/", 1)[0]
        if org in ("anthropic", "openai", "x-ai", "xai", "deepseek", "qwen", "google"):
            if org in ("x-ai", "xai"):
                return "xai"
            return org if org != "google" else "generic"
        if org in ("zhipu", "z-ai", "thudm"):
            return "glm"
        if org in ("minimax",):
            return "minimax"
        if org in ("moonshotai", "moonshot"):
            return "moonshot"
    if re.search(r"claude", m):
        return "anthropic"
    # GPT-5.x / Codex / o-series
    if re.search(r"gpt-|codex|o1|o3|o4-mini", m):
        return "openai"
    if re.search(r"grok", m):
        return "xai"
    if re.search(r"kimi|moonshot", m):
        return "kimi" if "kimi" in m else "moonshot"
    if re.search(r"deepseek", m):
        return "deepseek"
    if re.search(r"qwen|qwq", m):
        return "qwen"
    if re.search(r"glm|chatglm", m):
        return "glm"
    if re.search(r"minimax|abab", m):
        return "minimax"
    if re.search(r"mimo", m):
        return "mimo"
    return None


def resolve_profile(
    *,
    provider_id: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    llm_provider: str | None = None,
) -> ProviderProfile:
    """Resolve a ProviderProfile from catalog / snapshot fields."""
    pid = (provider_id or "").strip().lower()
    family: str | None = _PROVIDER_ID_MAP.get(pid) if pid else None
    # map 到 generic 不算锁定：继续用 url/model 推断真正厂商
    if family == "generic":
        family = None

    if family is None and llm_provider:
        lp = llm_provider.strip().lower()
        if lp == "anthropic":
            family = "anthropic"
        elif lp == "openai":
            family = "openai"
        elif lp == "ollama":
            family = "ollama"
        elif lp == "vllm":
            family = "vllm"

    if family is None:
        family = _family_from_url(base_url or "")
    if family is None:
        family = _family_from_model(model or "")
    if family is None:
        family = "generic"

    # OpenCode 网关：profile 用 opencode，缓存行为跟 openai-compatible
    if family == "opencode" and "opencode" not in _PROFILES:
        # 按模型再细分缓存策略（deepseek/gpt 等）
        nested = _family_from_model(model or "")
        if nested and nested in _PROFILES:
            return replace(
                _PROFILES[nested],
                family="opencode",
                protocol="openai-compatible",
            )
        family = "generic"

    base = _PROFILES.get(family) or _PROFILES["generic"]

    # OpenRouter: re-resolve by model for cache/limits hints
    if family == "openrouter" and model:
        nested = _family_from_model(model)
        if nested and nested != "openrouter" and nested in _PROFILES:
            inner = _PROFILES[nested]
            return replace(
                inner,
                family="openrouter",
                # keep openrouter protocol path
                protocol="openai-compatible",
            )

    return base


@lru_cache(maxsize=256)
def resolve_profile_cached(
    provider_id: str = "",
    base_url: str = "",
    model: str = "",
    llm_provider: str = "",
) -> ProviderProfile:
    return resolve_profile(
        provider_id=provider_id or None,
        base_url=base_url or None,
        model=model or None,
        llm_provider=llm_provider or None,
    )


def explicit_cache_enabled(profile: ProviderProfile) -> bool:
    """Whether to inject Anthropic-style cache_control for this profile."""
    try:
        from backend.core.config import settings
    except Exception:
        settings = None  # type: ignore

    mode = profile.cache_mode
    if mode == "none":
        return False
    if mode == "explicit_anthropic":
        if profile.family == "anthropic":
            return bool(getattr(settings, "agent_prompt_cache_anthropic", True)) if settings else True
        return True
    if mode == "both":
        if profile.family == "qwen":
            return bool(getattr(settings, "agent_prompt_cache_qwen_explicit", False)) if settings else False
        if profile.family == "minimax":
            return bool(getattr(settings, "agent_prompt_cache_minimax_explicit", False)) if settings else False
        return False
    return False


def apply_profile_to_service_attrs(service: Any, profile: ProviderProfile) -> None:
    """Attach profile + override sanitize limits on an LLM service instance."""
    service.profile = profile
    service._profile = profile
    if profile.force_temperature is not None:
        try:
            service.temperature = float(profile.force_temperature)
        except Exception:
            pass
    # tool truncate limits used by OpenAICompatibleService
    if hasattr(service, "_MAX_TOOL_ARG_CHARS"):
        type(service)._MAX_TOOL_ARG_CHARS = int(profile.max_tool_arg_chars)
    if hasattr(service, "_MAX_TOOL_RESULT_CHARS"):
        # instance-level preferred if we switch to that; class attr used today
        pass
    service._max_tool_arg_chars = int(profile.max_tool_arg_chars)
    service._max_tool_result_chars = int(profile.max_tool_result_chars)


def profile_as_dict(profile: ProviderProfile) -> dict[str, Any]:
    return {
        "family": profile.family,
        "protocol": profile.protocol,
        "cache_mode": profile.cache_mode,
        "default_context_window": profile.default_context_window,
        "prefer_billable_tokens": profile.prefer_billable_tokens,
        "l1_tool_chars": profile.l1_tool_chars,
        "l3_threshold_ratio": profile.l3_threshold_ratio,
        "tools_before_system": profile.tools_before_system,
        "force_temperature": profile.force_temperature,
    }
