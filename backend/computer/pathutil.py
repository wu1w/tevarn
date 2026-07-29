"""Computer / sandbox path helpers — Windows-safe agent keys."""

from __future__ import annotations

import re


def sanitize_agent_key_for_path(agent_key: str | None) -> str:
    """Map agent_key to a filesystem-safe directory name.

    Workforce uses keys like ``wf:{uuid}``; colon is illegal on Windows
    (``WinError 123`` when creating ``.computers/wf:.../home``).
    """
    raw = (agent_key or "main").strip() or "main"
    # Replace path separators and Windows-illegal chars: <>:"/\|?*
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw)
    cleaned = cleaned.strip(" .")
    if not cleaned:
        cleaned = "main"
    # Avoid reserved device names on Windows
    if cleaned.upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        cleaned = f"_{cleaned}"
    return cleaned[:180]
