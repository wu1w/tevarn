"""Pluggable context engines (Hermes-inspired ABC)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ContextEngine(ABC):
    """Controls compaction when approaching the model context limit."""

    last_prompt_tokens: int = 0
    last_completion_tokens: int = 0
    last_total_tokens: int = 0
    compression_count: int = 0
    context_length: int = 0
    threshold_percent: float = 0.72
    protect_first_n: int = 3
    protect_last_n: int = 12

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def update_from_response(self, usage: dict[str, Any] | None) -> None:
        ...

    @abstractmethod
    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        ...

    @abstractmethod
    async def compress(
        self,
        messages: list[dict[str, Any]],
        *,
        current_tokens: int | None = None,
        focus_topic: str | None = None,
        session_id: Any = None,
        allow_l5: bool = True,
        micro_only: bool = False,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Return (possibly shorter) messages + meta.

        allow_l5/micro_only: mid-loop tool rounds should disable L5 full summary.
        """
        ...

    def should_compress_preflight(self, messages: list[dict[str, Any]]) -> bool:
        return False

    def on_session_reset(self) -> None:
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.compression_count = 0

    def get_status(self) -> dict[str, Any]:
        return {
            "engine": self.name,
            "context_length": self.context_length,
            "threshold_percent": self.threshold_percent,
            "last_prompt_tokens": self.last_prompt_tokens,
            "last_completion_tokens": self.last_completion_tokens,
            "last_total_tokens": self.last_total_tokens,
            "compression_count": self.compression_count,
            "protect_first_n": self.protect_first_n,
            "protect_last_n": self.protect_last_n,
        }


# 全局默认（无 session 的脚本/单测）
_ENGINE: ContextEngine | None = None
# 按 session 隔离 L5 计数 / thrash / meter（P1：全局单例会株连所有并发会话）
_SESSION_ENGINES: dict[str, ContextEngine] = {}
_SESSION_ENGINE_MAX = 64


def get_context_engine(session_id: Any = None) -> ContextEngine:
    """取上下文引擎。传入 session_id 时 per-session 实例（长任务隔离 thrash/L5）。"""
    global _ENGINE
    from backend.agent.context_pipeline import PipelineContextEngine

    if session_id is None or str(session_id).strip() in ("", "None"):
        if _ENGINE is None:
            _ENGINE = PipelineContextEngine()
        return _ENGINE
    key = str(session_id).strip()
    eng = _SESSION_ENGINES.get(key)
    if eng is None:
        # 淘汰最旧（FIFO by insert order on py3.7+）
        while len(_SESSION_ENGINES) >= _SESSION_ENGINE_MAX:
            try:
                old_k = next(iter(_SESSION_ENGINES))
                _SESSION_ENGINES.pop(old_k, None)
            except StopIteration:
                break
        eng = PipelineContextEngine()
        _SESSION_ENGINES[key] = eng
    return eng


def reset_context_engine(session_id: Any = None) -> None:
    """重置全局或指定 session 的引擎。"""
    global _ENGINE
    if session_id is None or str(session_id).strip() in ("", "None"):
        _ENGINE = None
        return
    _SESSION_ENGINES.pop(str(session_id).strip(), None)


def reset_all_session_context_engines() -> None:
    _SESSION_ENGINES.clear()
