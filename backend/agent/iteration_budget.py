"""Per-run iteration budget — consume / refund / grace.

Aligned with Hermes IterationBudget semantics (simplified).
"""
from __future__ import annotations

import threading


class IterationBudget:
    """Thread-safe iteration counter for one agent run."""

    def __init__(self, max_total: int) -> None:
        self.max_total = max(1, int(max_total or 1))
        self._used = 0
        self._lock = threading.Lock()

    def consume(self) -> bool:
        """Try to consume one iteration. False if exhausted."""
        with self._lock:
            if self._used >= self.max_total:
                return False
            self._used += 1
            return True

    def refund(self) -> None:
        """Give back one iteration (e.g. cheap no-op turns)."""
        with self._lock:
            if self._used > 0:
                self._used -= 1

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self.max_total - self._used)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "used": self._used,
                "max_total": self.max_total,
                "remaining": max(0, self.max_total - self._used),
            }


__all__ = ["IterationBudget"]
