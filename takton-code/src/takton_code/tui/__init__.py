"""TUI package — lazy exports so non-TUI imports don't require rich."""

from __future__ import annotations

__all__ = ["TaktonCodeApp", "run_tui"]


def __getattr__(name: str):
    if name in ("TaktonCodeApp", "run_tui"):
        from takton_code.tui.app import TaktonCodeApp, run_tui

        return TaktonCodeApp if name == "TaktonCodeApp" else run_tui
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
