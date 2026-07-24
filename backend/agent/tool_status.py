"""Human-readable tool status lines for UI (while tools run)."""
from __future__ import annotations

import json
from typing import Any


def _short(v: Any, n: int = 48) -> str:
    s = str(v or "").replace("\n", " ").strip()
    if len(s) > n:
        return s[: n - 1] + "…"
    return s


def format_tool_status(name: str, arguments: dict[str, Any] | None = None) -> str:
    """e.g. 🔍 search: python asyncio  /  📄 file_read: src/main.py"""
    args = arguments if isinstance(arguments, dict) else {}
    # unwrap string JSON
    if len(args) == 1 and isinstance(next(iter(args.values()), None), str):
        pass
    icons = {
        "search": "🔍",
        "web_search": "🔍",
        "file_read": "📄",
        "file_write": "✍️",
        "edit": "✏️",
        "grep": "🔎",
        "glob": "📁",
        "command": "💻",
        "python": "🐍",
        "browser": "🌐",
        "http": "🌐",
        "apply_patch": "🩹",
    }
    icon = icons.get(name, "🔧")
    # pick primary arg
    primary = (
        args.get("query")
        or args.get("filepath")
        or args.get("path")
        or args.get("pattern")
        or args.get("command")
        or args.get("url")
        or args.get("content")
        or ""
    )
    if not primary and args:
        # first short scalar
        for k, v in args.items():
            if k.startswith("_"):
                continue
            if isinstance(v, (str, int, float)):
                primary = f"{k}={v}"
                break
    detail = _short(primary, 56)
    if detail:
        return f"{icon} {name}: {detail}"
    return f"{icon} {name}"


def format_tool_done(name: str, ok: bool = True) -> str:
    mark = "✓" if ok else "✗"
    return f"{mark} {name}"
