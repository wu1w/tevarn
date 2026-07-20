"""Token 估算工具。

提供简单的 token 计数估算，用于上下文管理和压缩。

本文件自 Windows 一键包 (resources/backend/agent/token_meter.py) 恢复，
并在此基础上补全了 repo 测试与 context_pipeline 期望的新版接口：
  - update_from_response / last_*_tokens / remaining()
  - 无参 should_compress()
  - 更健壮的 estimate_messages（覆盖 tool_calls / 多模态 / 非字符串 content）
  - 对异常 context_window / threshold_percent 输入做防御性钳制
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 防御性边界：context_window 合理区间
_MIN_CONTEXT_WINDOW = 512
_MAX_CONTEXT_WINDOW = 10_000_000
_DEFAULT_CONTEXT_WINDOW = 128_000
# threshold_percent 合理区间 (0, 1]
_MIN_THRESHOLD = 0.05
_MAX_THRESHOLD = 1.0
_DEFAULT_THRESHOLD = 0.8


def _clamp_context_window(value: Any) -> int:
    """把外部传入的 context_window 钳制到合理区间，防呆。"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_CONTEXT_WINDOW
    if v < _MIN_CONTEXT_WINDOW:
        return _MIN_CONTEXT_WINDOW
    if v > _MAX_CONTEXT_WINDOW:
        return _MAX_CONTEXT_WINDOW
    return v


def _clamp_threshold(value: Any) -> float | None:
    """把 threshold_percent 钳制到 (0,1]；非法输入返回 None（用默认）。"""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    if f < _MIN_THRESHOLD:
        return _MIN_THRESHOLD
    if f > _MAX_THRESHOLD:
        return _MAX_THRESHOLD
    return f


class TokenMeter:
    """Token 估算器。

    使用简单启发式规则估算 token 数量：
    - 英文约 1 token ≈ 4 字符
    - 中文约 1 token ≈ 1.5 字符
    - 代码/JSON 按保守估计
    """

    def __init__(self, context_window: int = 128_000, threshold_percent: float | None = None) -> None:
        """初始化 TokenMeter。

        Args:
            context_window: 上下文窗口大小（token 数），异常值会被钳制到合理区间
            threshold_percent: 可选的实例级默认压缩阈值（0~1）。
                若提供，调用 ``should_compress`` 未显式传 threshold_ratio 时使用该值。
        """
        self.context_window = _clamp_context_window(context_window)
        self.threshold_percent = _clamp_threshold(threshold_percent)

        # 使用量追踪（由 update_from_response 回写）
        self.last_prompt_tokens: int = 0
        self.last_completion_tokens: int = 0
        self.last_total_tokens: int = 0

    # ──────────────────────────────────────────────────────────
    # 阈值
    # ──────────────────────────────────────────────────────────
    @property
    def threshold_tokens(self) -> int:
        """触发压缩的 token 阈值（context_window * threshold_percent，默认 0.8）。"""
        ratio = self.threshold_percent if self.threshold_percent is not None else _DEFAULT_THRESHOLD
        return int(self.context_window * ratio)

    def remaining(self, current_tokens: int | None = None) -> int:
        """距离上下文窗口上限还剩多少 token。

        Args:
            current_tokens: 已用 token 数；缺省用最近一次 update_from_response
                记录的 prompt_tokens。

        Returns:
            剩余 token 数（>= 0）。
        """
        used = current_tokens if current_tokens is not None else self.last_prompt_tokens
        try:
            used = int(used)
        except (TypeError, ValueError):
            used = 0
        return max(0, self.context_window - used)

    # ──────────────────────────────────────────────────────────
    # 使用量回写
    # ──────────────────────────────────────────────────────────
    def update_from_response(self, usage: dict[str, Any] | None) -> None:
        """从 LLM 响应（或本地估算）回写 token 用量，驱动后续压缩判断。

        兼容 OpenAI 风格 usage 字段；任何字段缺失/非法都安全忽略，不抛异常。

        Args:
            usage: 形如 {"prompt_tokens": int, "completion_tokens": int,
                "total_tokens": int} 的字典；可为 None 或非 dict。
        """
        if not isinstance(usage, dict):
            return

        def _as_int(v: Any) -> int | None:
            try:
                i = int(v)
            except (TypeError, ValueError):
                return None
            return i if i >= 0 else None

        p = _as_int(usage.get("prompt_tokens"))
        c = _as_int(usage.get("completion_tokens"))
        t = _as_int(usage.get("total_tokens"))

        if p is not None:
            self.last_prompt_tokens = p
        if c is not None:
            self.last_completion_tokens = c
        if t is not None:
            self.last_total_tokens = t
        elif p is not None or c is not None:
            # 缺 total 时用 p+c 合成，保持 last_total_tokens 单调可用
            self.last_total_tokens = (p or self.last_prompt_tokens) + (
                c or self.last_completion_tokens
            )

    # ──────────────────────────────────────────────────────────
    # 状态
    # ──────────────────────────────────────────────────────────
    def get_status(self) -> dict[str, Any]:
        """返回 TokenMeter 当前状态（供 context engine-status 路由聚合）。"""
        return {
            "context_window": self.context_window,
            "threshold_percent": self.threshold_percent if self.threshold_percent is not None else _DEFAULT_THRESHOLD,
            "threshold_tokens": self.threshold_tokens,
            "last_prompt_tokens": self.last_prompt_tokens,
            "last_completion_tokens": self.last_completion_tokens,
            "last_total_tokens": self.last_total_tokens,
            "remaining": self.remaining(),
        }

    # ──────────────────────────────────────────────────────────
    # 估算
    # ──────────────────────────────────────────────────────────
    def estimate_text(self, text: str) -> int:
        """估算文本的 token 数量。

        Args:
            text: 输入文本

        Returns:
            估算的 token 数量
        """
        if not text:
            return 0

        # 统计中文字符数
        chinese_chars = sum(1 for c in text if "一" <= c <= "鿿")
        # 统计其他字符数
        other_chars = len(text) - chinese_chars

        # 中文按 1.5 字符/token，其他按 4 字符/token
        tokens = (chinese_chars / 1.5) + (other_chars / 4.0)

        # 保守估计，向上取整
        return max(1, int(tokens) + 1)

    def estimate_messages(self, messages: list[dict[str, Any]]) -> int:
        """估算消息列表的总 token 数量。

        覆盖以下情形，避免在工具密集 / 多模态场景下严重低估：
          - 普通字符串 content
          - 多模态 list content（text 部分）
          - assistant 的 tool_calls（函数名 + 参数 JSON）
          - 非字符串 content（dict/list 序列化为 JSON 估算）
          - 非 dict 消息（跳过）

        Args:
            messages: 消息列表，每个消息是 dict 包含 role/content

        Returns:
            估算的总 token 数量
        """
        if not isinstance(messages, (list, tuple)):
            return 0

        total = 0
        for msg in messages:
            if not isinstance(msg, dict):
                continue

            # 每条消息有 role + 格式开销
            total += 4  # role + 格式开销

            content = msg.get("content", "")
            if isinstance(content, str):
                total += self.estimate_text(content)
            elif isinstance(content, list):
                # 多模态内容
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total += self.estimate_text(part.get("text", ""))
                    elif isinstance(part, dict):
                        # image_url / 其他 part：给一个保守固定开销
                        total += 16
            elif content is not None:
                # dict / 其他可序列化对象：序列化后估算，避免漏算
                total += self.estimate_text(self._safe_str(content))

            # tool_calls 开销（函数名 + 参数），工具密集场景不可忽略
            tool_calls = msg.get("tool_calls")
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function") or {}
                    if isinstance(fn, dict):
                        name = fn.get("name")
                        if name:
                            total += self.estimate_text(str(name))
                        args = fn.get("arguments")
                        if args:
                            total += self.estimate_text(self._safe_str(args))
                    total += 4  # tool_call 结构开销

        return total

    @staticmethod
    def _safe_str(value: Any) -> str:
        """把任意值安全转成字符串用于估算（优先 JSON，失败用 str）。"""
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            try:
                return str(value)
            except Exception:
                return ""

    # ──────────────────────────────────────────────────────────
    # 压缩判断
    # ──────────────────────────────────────────────────────────
    def should_compress(self, current_tokens: int | None = None, threshold_ratio: float | None = None) -> bool:
        """判断是否需要压缩上下文。

        Args:
            current_tokens: 当前 token 数；缺省用最近一次记录的 prompt_tokens。
            threshold_ratio: 触发压缩的阈值比例；若为 None 则使用实例的
                ``threshold_percent``，两者都未提供时回退到 0.8。

        Returns:
            是否需要压缩
        """
        if current_tokens is None:
            current_tokens = self.last_prompt_tokens
        try:
            current_tokens = int(current_tokens)
        except (TypeError, ValueError):
            return False

        if threshold_ratio is None:
            threshold_ratio = self.threshold_percent if self.threshold_percent is not None else _DEFAULT_THRESHOLD
        else:
            threshold_ratio = _clamp_threshold(threshold_ratio) or _DEFAULT_THRESHOLD
        return current_tokens >= int(self.context_window * threshold_ratio)
