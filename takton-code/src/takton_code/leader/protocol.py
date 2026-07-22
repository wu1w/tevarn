"""TCP leader protocol — localhost only, no auth token (用户 recommendation)."""

from __future__ import annotations

import json
from typing import Any


PROTOCOL_VERSION = 1


def encode(msg: dict[str, Any]) -> bytes:
    return (json.dumps(msg, ensure_ascii=False, default=str) + "\n").encode("utf-8")


def decode_line(line: bytes | str) -> dict[str, Any] | None:
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    line = line.strip()
    if not line:
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def hello_ok(*, sessions: list[dict[str, Any]], leader_version: str = "0.1.0") -> dict[str, Any]:
    return {
        "op": "hello_ok",
        "protocol": PROTOCOL_VERSION,
        "leader_version": leader_version,
        "sessions": sessions,
    }
