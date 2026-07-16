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
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Return (possibly shorter) messages + meta."""
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


_ENGINE: ContextEngine | None = None


def get_context_engine() -> ContextEngine:
    global _ENGINE
    if _ENGINE is None:
        from backend.agent.context_pipeline import PipelineContextEngine

        _ENGINE = PipelineContextEngine()
    return _ENGINE


def reset_context_engine() -> None:
    global _ENGINE
    _ENGINE = None
