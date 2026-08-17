"""WebSocket first-frame handshake helpers.

The chat WS used to `receive_text()` once for auth and discard anything else.
Loopback clients send `sync` as the first packet (no token); token clients wait
for `auth_ok` that was never sent after connect. Keep classification here so
the endpoint and tests share one contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class FirstFrame:
    """Result of classifying the post-accept first WebSocket text frame."""

    is_auth: bool
    token: str = ""
    pending_raw: str | None = None


def parse_first_ws_frame(raw: str | None) -> FirstFrame:
    """Classify the first text frame after `accept()`.

    - `{"type":"auth",...}` is consumed as credentials (token may be empty).
    - Any other payload (sync / ping / user_input / invalid JSON) is returned
      as `pending_raw` for the main message loop — never dropped.
    """
    if raw is None or not str(raw).strip():
        return FirstFrame(is_auth=False, pending_raw=None)
    text = str(raw)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return FirstFrame(is_auth=False, pending_raw=text)
    if isinstance(data, dict) and data.get("type") == "auth":
        token = data.get("token", "")
        return FirstFrame(is_auth=True, token="" if token is None else str(token))
    return FirstFrame(is_auth=False, pending_raw=text)


def auth_ok_payload(user_id) -> dict:
    """Handshake ack so the client can sync without the 1.5s fallback."""
    return {
        "type": "auth_ok",
        "user_id": str(user_id) if user_id else None,
    }
