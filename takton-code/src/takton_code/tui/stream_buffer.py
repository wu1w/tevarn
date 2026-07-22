"""Typewriter stream buffer — throttle by chars and/or ms."""

from __future__ import annotations

import time


class StreamBuffer:
    def __init__(self, flush_chars: int = 1, flush_ms: int = 16) -> None:
        self.flush_chars = max(1, int(flush_chars))
        self.flush_ms = max(0, int(flush_ms))
        self.buf = ""
        self._last = time.monotonic()
        self._started = False

    def push(self, s: str) -> str | None:
        """Append; return segment to paint when threshold hit, else None."""
        if not s:
            return None
        self.buf += s
        now = time.monotonic()
        if not self._started:
            self._started = True
            self._last = now
            # first chunk: only flush if chars threshold met
            if len(self.buf) >= self.flush_chars:
                out, self.buf = self.buf, ""
                self._last = now
                return out
            return None
        elapsed_ms = (now - self._last) * 1000.0
        if len(self.buf) >= self.flush_chars or (
            self.flush_ms > 0 and elapsed_ms >= self.flush_ms
        ):
            out, self.buf = self.buf, ""
            self._last = now
            return out
        return None

    def flush(self) -> str:
        out, self.buf = self.buf, ""
        self._last = time.monotonic()
        return out

    @property
    def pending(self) -> str:
        return self.buf
