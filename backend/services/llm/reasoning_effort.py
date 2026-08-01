"""Map user-facing reasoning_effort to provider-specific request fields.

Values: off | low | medium | high | max (| xhigh | minimal)
Only injects when the model/family is likely to accept the params, so plain
chat models don't get 400 from unknown fields.
"""

from __future__ import annotations

import re
from typing import Any


_OFF = frozenset({"off", "none", "disabled", "false", "0", ""})


def normalize_effort(raw: Any, *, default: str = "medium") -> str:
    s = str(raw or "").strip().lower()
    if not s:
        return default
    if s in ("disable", "disabled", "false", "0", "none"):
        return "off"
    if s in ("off", "low", "medium", "high", "max", "xhigh", "minimal"):
        return s
    return default


def supports_reasoning_control(*, model: str | None, family: str | None = None) -> bool:
    """Only inject when the model is likely to accept thinking/effort params.

    Plain chat models (gpt-4o, deepseek-chat, …) must not receive unknown fields
    that cause HTTP 400 on strict gateways.
    """
    fam = (family or "").strip().lower()
    name = (model or "").strip().lower()
    if not name and fam not in ("deepseek", "anthropic", "qwen"):
        return False
    # Family-wide only for hybrid-thinking providers that document the param broadly
    if fam in {"deepseek", "qwen", "anthropic"}:
        return True
    return bool(
        re.search(
            r"reason|think|o1\b|o3\b|o4-mini|r1\b|qwq|deepseek|v4|gpt-5|"
            r"claude|grok-3|grok-4|kimi|glm-4\.5|glm-5|qwen3|hy3",
            name or "",
            re.I,
        )
    )


def _openai_effort(effort: str) -> str:
    if effort in ("max", "xhigh"):
        return "high"
    if effort == "off":
        return "low"
    if effort == "minimal":
        return "low"
    return effort if effort in ("low", "medium", "high") else "medium"


def _deepseek_effort(effort: str) -> str:
    # DeepSeek V4: low | high | max
    if effort in ("off",):
        return "low"
    if effort in ("medium", "xhigh"):
        return "high"
    if effort in ("low", "high", "max"):
        return effort
    return "high"


def _anthropic_budget(effort: str) -> int:
    return {
        "off": 0,
        "low": 2048,
        "minimal": 1024,
        "medium": 8000,
        "high": 16000,
        "max": 32000,
        "xhigh": 32000,
    }.get(effort, 8000)


def apply_reasoning_effort(
    payload: dict[str, Any],
    *,
    effort: str | None,
    model: str | None = None,
    family: str | None = None,
    force: bool = False,
) -> bool:
    """Mutate chat/completions payload. Returns True if any field was written."""
    if not force and not supports_reasoning_control(model=model, family=family):
        return False

    e = normalize_effort(effort)
    fam = (family or "").strip().lower()
    name = (model or "").strip().lower()
    wrote = False

    is_deepseek = fam == "deepseek" or "deepseek" in name
    is_qwen = fam == "qwen" or bool(re.search(r"qwen|qwq", name))
    is_anthropic = fam == "anthropic" or "claude" in name
    is_openaiish = fam in ("openai", "xai", "openrouter", "generic", "kimi", "moonshot", "glm", "") or not is_deepseek

    if is_deepseek:
        if e in _OFF:
            payload["thinking"] = {"type": "disabled"}
        else:
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = _deepseek_effort(e)
        wrote = True
        return wrote

    if is_qwen:
        enabled = e not in _OFF
        payload["enable_thinking"] = enabled
        ctk = payload.get("chat_template_kwargs")
        if not isinstance(ctk, dict):
            ctk = {}
        ctk = {**ctk, "enable_thinking": enabled}
        payload["chat_template_kwargs"] = ctk
        # Some gateways also accept OpenAI-style effort when thinking is on
        if enabled:
            payload["reasoning_effort"] = _openai_effort(e)
        wrote = True
        return wrote

    if is_anthropic:
        # Anthropic Messages API shape; openai-compat proxies may ignore
        budget = _anthropic_budget(e)
        if budget <= 0:
            payload.pop("thinking", None)
        else:
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        wrote = True
        return wrote

    if is_openaiish:
        if e in _OFF:
            # o-series often has no true off; use minimal/low when present
            payload["reasoning_effort"] = "low"
        else:
            payload["reasoning_effort"] = _openai_effort(e)
        # OpenRouter nested form (harmless if ignored)
        if fam == "openrouter":
            payload["reasoning"] = {"effort": payload["reasoning_effort"]}
        wrote = True

    return wrote
