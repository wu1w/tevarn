"""Runtime doom-loop detection — same tool + similar args repeated N times → ask/stop."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DoomLoopGuard:
    """Track consecutive identical tool fingerprints within a turn.

    When the same (name, args_hash) appears ``threshold`` times in a row,
    ``should_ask`` becomes True and callers should pause for user confirmation
    or inject a steer note (Grok/Claude-style thrash protection for tools).
    """

    threshold: int = 3
    window: list[tuple[str, float]] = field(default_factory=list)
    last_fingerprint: str | None = None
    streak: int = 0
    tripped: bool = False
    last_tool: str | None = None

    @staticmethod
    def fingerprint(name: str, arguments: Any) -> str:
        if isinstance(arguments, dict):
            raw = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        else:
            raw = str(arguments or "")
        # normalize whitespace for near-dupes
        raw = " ".join(raw.split())
        h = hashlib.sha1(f"{name}|{raw}".encode("utf-8", errors="replace")).hexdigest()[:16]
        return f"{name}:{h}"

    def record(self, name: str, arguments: Any) -> bool:
        """Record a tool call. Returns True if doom-loop just tripped."""
        fp = self.fingerprint(name, arguments)
        now = time.time()
        self.window.append((fp, now))
        # keep last 20
        self.window = self.window[-20:]
        if fp == self.last_fingerprint:
            self.streak += 1
        else:
            self.last_fingerprint = fp
            self.streak = 1
        self.last_tool = name
        if self.streak >= max(2, self.threshold):
            self.tripped = True
            return True
        return False

    def reset_turn(self) -> None:
        self.window.clear()
        self.last_fingerprint = None
        self.streak = 0
        self.tripped = False
        self.last_tool = None

    def clear_trip(self) -> None:
        self.tripped = False
        self.streak = 0
        self.last_fingerprint = None

    def status(self) -> dict[str, Any]:
        return {
            "tripped": self.tripped,
            "streak": self.streak,
            "threshold": self.threshold,
            "last_tool": self.last_tool,
            "last_fingerprint": self.last_fingerprint,
        }
