"""Turn-level retry taxonomy for the agent loop.

Classifies failures and decides whether to continue / force final / stop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RetryKind(str, Enum):
    EMPTY_CONTENT = "empty_content"
    EMPTY_TOOL_NAME = "empty_tool_name"
    TRUNCATED_TOOL = "truncated_tool"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_TRANSIENT = "tool_transient"
    RATE_LIMIT = "rate_limit"
    CONTENT_FILTER = "content_filter"
    THRASH = "thrash"
    UNKNOWN = "unknown"


# default caps per kind (per run)
_DEFAULT_CAPS: dict[RetryKind, int] = {
    RetryKind.EMPTY_CONTENT: 2,
    RetryKind.EMPTY_TOOL_NAME: 2,
    RetryKind.TRUNCATED_TOOL: 2,
    RetryKind.TOOL_TIMEOUT: 2,
    RetryKind.TOOL_TRANSIENT: 3,
    RetryKind.RATE_LIMIT: 3,
    RetryKind.CONTENT_FILTER: 0,  # do not burn retries on policy blocks
    RetryKind.THRASH: 1,
    RetryKind.UNKNOWN: 1,
}


@dataclass
class TurnRetryState:
    """Counts retryable events and recommends next action."""

    caps: dict[RetryKind, int] = field(default_factory=lambda: dict(_DEFAULT_CAPS))
    counts: dict[str, int] = field(default_factory=dict)
    last_kind: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def record(self, kind: RetryKind | str, *, detail: str = "") -> dict[str, Any]:
        k = kind.value if isinstance(kind, RetryKind) else str(kind)
        self.counts[k] = int(self.counts.get(k, 0)) + 1
        self.last_kind = k
        ev = {"kind": k, "n": self.counts[k], "detail": (detail or "")[:200]}
        self.events.append(ev)
        return ev

    def _cap(self, kind: RetryKind) -> int:
        return int(self.caps.get(kind, _DEFAULT_CAPS.get(kind, 1)))

    def can_retry(self, kind: RetryKind | str) -> bool:
        kk = kind if isinstance(kind, RetryKind) else RetryKind(str(kind))
        n = int(self.counts.get(kk.value, 0))
        return n < self._cap(kk)

    def note_and_decide(
        self,
        kind: RetryKind | str,
        *,
        detail: str = "",
    ) -> str:
        """Record event; return action: retry | force_final | stop."""
        if isinstance(kind, RetryKind):
            kk = kind
        else:
            try:
                kk = RetryKind(str(kind))
            except ValueError:
                kk = RetryKind.UNKNOWN
        self.record(kk, detail=detail)
        n = int(self.counts.get(kk.value, 0))
        cap = self._cap(kk)
        if kk == RetryKind.CONTENT_FILTER:
            return "stop"
        if kk == RetryKind.THRASH:
            return "force_final" if n >= 1 else "retry"
        if n <= cap:
            return "retry"
        if kk in (
            RetryKind.EMPTY_CONTENT,
            RetryKind.EMPTY_TOOL_NAME,
            RetryKind.TRUNCATED_TOOL,
        ):
            return "force_final"
        return "stop"

    def snapshot(self) -> dict[str, Any]:
        return {
            "counts": dict(self.counts),
            "last_kind": self.last_kind,
            "events": list(self.events[-12:]),
        }


def classify_llm_error(err: BaseException | str) -> RetryKind:
    text = str(err).lower()
    if any(x in text for x in ("rate limit", "429", "too many requests", "overload")):
        return RetryKind.RATE_LIMIT
    if any(x in text for x in ("content_filter", "content policy", "safety")):
        return RetryKind.CONTENT_FILTER
    if "timeout" in text or "timed out" in text:
        return RetryKind.TOOL_TIMEOUT
    if any(x in text for x in ("502", "503", "504", "temporarily", "connection reset")):
        return RetryKind.TOOL_TRANSIENT
    return RetryKind.UNKNOWN


def classify_tool_result(result: str | None) -> RetryKind | None:
    """Return retry kind if tool result looks like failure worth classifying."""
    t = (result or "").strip()
    if not t:
        return None
    low = t.lower()
    if t.startswith("[Error]") or t.startswith("[error]"):
        if "timeout" in low or "timed out" in low:
            return RetryKind.TOOL_TIMEOUT
        if any(x in low for x in ("429", "rate limit", "503", "502", "temporarily")):
            return RetryKind.TOOL_TRANSIENT
        return None  # fatal tool error — not auto-retry whole turn
    if "truncated" in low and "tool" in low:
        return RetryKind.TRUNCATED_TOOL
    return None


__all__ = [
    "RetryKind",
    "TurnRetryState",
    "classify_llm_error",
    "classify_tool_result",
]
