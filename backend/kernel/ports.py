"""Kernel outbound ports — avoid importing FastAPI / api.routes from kernel.

Adapters (main lifespan, HTTP) register implementations at boot.
"""

from __future__ import annotations

from typing import Any, Callable

_ws_manager: Any = None
_event_sink: Callable[..., Any] | None = None


def set_ws_manager(manager: Any) -> None:
    global _ws_manager
    _ws_manager = manager


def get_ws_manager() -> Any:
    return _ws_manager


def set_event_sink(fn: Callable[..., Any] | None) -> None:
    global _event_sink
    _event_sink = fn


def get_event_sink() -> Callable[..., Any] | None:
    return _event_sink
