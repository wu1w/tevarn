"""TUI package — lazy exports so non-TUI imports don't require rich."""

from __future__ import annotations

__all__ = ["TevarnCodeApp", "run_tui"]


def __getattr__(name: str):
    if name in ("TevarnCodeApp", "run_tui"):
        from tevarn_code.tui.app import TevarnCodeApp, run_tui

        return TevarnCodeApp if name == "TevarnCodeApp" else run_tui
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
