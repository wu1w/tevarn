"""
按模型名推断上下文窗口与建议 max_tokens。
选中模型时写入 settings.context_window / max_tokens。
"""

from __future__ import annotations

import re
from typing import Any


# (pattern, context_window) — 更具体的规则写在前面
_MODEL_CONTEXT_RULES: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"grok-4|grok-3|grok-2|grok-beta", re.I), 131_072),
    (re.compile(r"gpt-4o|gpt-4\.1|o3|o4-mini|o1", re.I), 128_000),
    (re.compile(r"gpt-4-turbo|gpt-4-0125|gpt-4-1106", re.I), 128_000),
    (re.compile(r"gpt-4(?!o)", re.I), 8_192),
    (re.compile(r"gpt-3\.5|gpt-35", re.I), 16_384),
    (re.compile(r"claude-3\.5|claude-3-5|claude-4|claude-sonnet-4|claude-opus-4|claude-haiku-4", re.I), 200_000),
    (re.compile(r"claude-3-opus|claude-3-sonnet|claude-3-haiku", re.I), 200_000),
    (re.compile(r"claude-2", re.I), 100_000),
    (re.compile(r"gemini-2|gemini-1\.5", re.I), 1_000_000),
    (re.compile(r"gemini", re.I), 128_000),
    (re.compile(r"deepseek|qwen2\.5|qwen3|qwq", re.I), 128_000),
    (re.compile(r"qwen", re.I), 32_768),
    (re.compile(r"llama-?3\.1|llama-?3\.2|llama-?3\.3|llama4", re.I), 128_000),
    (re.compile(r"llama-?3|llama3", re.I), 8_192),
    (re.compile(r"mistral|mixtral|command-r", re.I), 32_768),
    (re.compile(r"kimi|moonshot", re.I), 128_000),
    (re.compile(r"glm-4|chatglm", re.I), 128_000),
]


def infer_context_window(model: str | None, fallback: int = 128_000) -> int:
    name = (model or "").strip()
    if not name:
        return fallback
    for pat, window in _MODEL_CONTEXT_RULES:
        if pat.search(name):
            return window
    return fallback


def suggest_max_tokens(context_window: int, floor: int = 4_096, ceiling: int = 16_384) -> int:
    """
    生成上限：取 context 的 1/8，夹在 [floor, ceiling]，默认倾向 12K。
    """
    suggested = max(floor, min(ceiling, context_window // 8))
    # 至少 12K（若窗口允许）
    if context_window >= 24_000:
        suggested = max(suggested, 12_288)
    return min(suggested, context_window // 2, ceiling)


def limits_for_model(model: str | None) -> dict[str, Any]:
    ctx = infer_context_window(model)
    return {
        "context_window": ctx,
        "max_tokens": suggest_max_tokens(ctx),
    }
