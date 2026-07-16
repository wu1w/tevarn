"""Shared protocol constants for takton-agent (kept in sync with backend.services.remote.protocol)."""

from __future__ import annotations

PROTOCOL_VERSION = 1
DEFAULT_AGENT_PORT = 19876
METHODS = frozenset({"hello", "ping", "file.list", "file.read", "exec.run", "capabilities"})
