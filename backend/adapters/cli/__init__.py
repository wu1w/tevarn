"""CLI adapter — re-export entry for topology (OS layering).

Canonical implementation: ``backend.cli`` / ``python -m backend.cli``.
"""

from __future__ import annotations

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    from backend.cli import main as _main

    return _main(argv)
