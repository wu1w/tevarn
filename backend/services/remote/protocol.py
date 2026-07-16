"""Takton remote agent protocol (L1 MVP).

Transport: JSON messages over WebSocket.
Each request:
  {"id": "uuid", "method": "ping|file.list|file.read|exec.run", "params": {...}}
Each response:
  {"id": "uuid", "ok": true, "result": {...}}
  {"id": "uuid", "ok": false, "error": {"code": "...", "message": "..."}}

Auth: first client message must be:
  {"id": "...", "method": "hello", "params": {"token": "...", "name": "...", "capabilities": [...]}}
Or query string ?token= on connect (agent validates).
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict


PROTOCOL_VERSION = 1
DEFAULT_AGENT_PORT = 19876


class RpcRequest(TypedDict, total=False):
    id: str
    method: str
    params: dict[str, Any]


class RpcError(TypedDict):
    code: str
    message: str


class RpcResponse(TypedDict, total=False):
    id: str
    ok: bool
    result: Any
    error: RpcError


METHODS = frozenset({"hello", "ping", "file.list", "file.read", "exec.run", "capabilities"})
