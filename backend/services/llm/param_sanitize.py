# -*- coding: utf-8 -*-
"""LLM 生成参数的防御性钳制。

统一的 sanitize 入口，确保任何来源（用户手填 / 会话快照 / 默认值 /
环境变量）的 max_tokens / temperature 在进入各 provider 的 API payload
之前都被钳制到安全区间，避免因非法值导致 provider 400 / 异常。

设计原则：
- 绝不抛异常：任何非法输入都回退到安全默认，保证 chat 不崩
- 边界明确：max_tokens ∈ [1, 模型上限]，temperature ∈ [0, 2]
- provider 差异：Anthropic 等部分 provider 对 max_tokens 有硬性输出上限
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── max_tokens 边界 ──────────────────────────────────────────
# 绝对下限：至少允许生成 1 个 token（否则等于禁言）
MIN_MAX_TOKENS = 1
# 绝对上限：防止用户填一个天文数字把请求打爆 / 触发 provider 计费异常
MAX_MAX_TOKENS_CAP = 1_000_000
# 默认回退值
DEFAULT_MAX_TOKENS = 4096

# ── temperature 边界 ─────────────────────────────────────────
MIN_TEMPERATURE = 0.0
MAX_TEMPERATURE = 2.0
DEFAULT_TEMPERATURE = 0.7

# 部分 provider/模型的硬性输出上限（超出会直接 400）。
# 键为小写匹配子串；值为该模型允许的最大 max_tokens。
# 只列已知有硬限制且远小于 context_window 的；其余交给通用 cap。
_MODEL_OUTPUT_CAPS: list[tuple[str, int]] = [
    ("claude-3-5-sonnet", 8192),
    ("claude-3-sonnet", 4096),
    ("claude-3-haiku", 4096),
    ("claude-3-opus", 4096),
    ("gpt-3.5", 4096),
]


def _model_output_cap(model: str | None) -> int | None:
    """按模型名返回已知硬输出上限；未知返回 None。"""
    name = (model or "").lower()
    for sub, cap in _MODEL_OUTPUT_CAPS:
        if sub in name:
            return cap
    return None


def sanitize_max_tokens(
    value: Any,
    *,
    model: str | None = None,
    context_window: Any = None,
    default: int = DEFAULT_MAX_TOKENS,
) -> int:
    """把任意来源的 max_tokens 钳制到安全、provider 可接受的整数。

    处理顺序：
      1. 无法解析为数字 → 回退 default
      2. 负数 / 0 → 钳到 MIN_MAX_TOKENS
      3. 超过绝对上限 → 钳到 MAX_MAX_TOKENS_CAP
      4. 超过模型硬输出上限（如 claude-3-sonnet 8192）→ 钳到该上限
      5. 若给了 context_window，max_tokens 不应超过它（留出生成空间无意义）

    永不抛异常。

    Args:
        value: 原始 max_tokens（可能是 int/float/str/None/垃圾值）
        model: 模型名，用于匹配已知硬输出上限
        context_window: 上下文窗口，可选，用于进一步约束
        default: 解析失败时的回退值

    Returns:
        安全可用的 max_tokens（>= MIN_MAX_TOKENS）
    """
    # 1. 解析
    n: int | None = None
    if isinstance(value, bool):  # bool 是 int 子类，单独挡掉
        n = None
    elif isinstance(value, (int, float)):
        try:
            n = int(value)
        except (TypeError, ValueError, OverflowError):
            n = None
    elif isinstance(value, str):
        s = value.strip()
        if s:
            try:
                n = int(float(s))  # 容忍 "4096.0" / "4096"
            except (TypeError, ValueError, OverflowError):
                n = None

    if n is None:
        if value not in (None, ""):
            logger.warning("max_tokens 非法值 %r，回退默认 %d", value, default)
        n = default

    # 2. 下限
    if n < MIN_MAX_TOKENS:
        logger.warning("max_tokens=%d 过小，钳到 %d", n, MIN_MAX_TOKENS)
        n = MIN_MAX_TOKENS

    # 3. 绝对上限
    if n > MAX_MAX_TOKENS_CAP:
        logger.warning("max_tokens=%d 超绝对上限，钳到 %d", n, MAX_MAX_TOKENS_CAP)
        n = MAX_MAX_TOKENS_CAP

    # 4. 模型硬输出上限
    cap = _model_output_cap(model)
    if cap is not None and n > cap:
        logger.warning("max_tokens=%d 超模型 %s 硬上限，钳到 %d", n, model, cap)
        n = cap

    # 5. 不超过 context_window（若提供且可解析）
    cw = _safe_int(context_window)
    if cw is not None and cw > 0 and n > cw:
        logger.warning("max_tokens=%d 超 context_window=%d，钳到 %d", n, cw, cw)
        n = max(MIN_MAX_TOKENS, cw)

    return n


def sanitize_temperature(value: Any, *, default: float = DEFAULT_TEMPERATURE) -> float:
    """把 temperature 钳制到 [0, 2]，非法回退默认。永不抛异常。"""
    f: float | None = None
    if isinstance(value, bool):
        f = None
    elif isinstance(value, (int, float)):
        try:
            f = float(value)
        except (TypeError, ValueError, OverflowError):
            f = None
    elif isinstance(value, str):
        s = value.strip()
        if s:
            try:
                f = float(s)
            except (TypeError, ValueError, OverflowError):
                f = None

    if f is None or f != f:  # None 或 NaN
        if value not in (None, ""):
            logger.warning("temperature 非法值 %r，回退默认 %.2f", value, default)
        f = default

    if f < MIN_TEMPERATURE:
        logger.warning("temperature=%.3f 过低，钳到 %.1f", f, MIN_TEMPERATURE)
        f = MIN_TEMPERATURE
    if f > MAX_TEMPERATURE:
        logger.warning("temperature=%.3f 过高，钳到 %.1f", f, MAX_TEMPERATURE)
        f = MAX_TEMPERATURE
    return f


def _safe_int(value: Any) -> int | None:
    """安全转 int，失败返回 None。"""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None
