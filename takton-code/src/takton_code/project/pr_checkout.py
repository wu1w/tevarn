"""Lightweight GitHub PR checkout helper (uses gh CLI when available)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any


def gh_available() -> bool:
    return shutil.which("gh") is not None


def checkout_pr(project_root: Path, pr: str) -> dict[str, Any]:
    """
    pr: number or URL.
    Runs: gh pr checkout <pr>
    """
    root = Path(project_root).resolve()
    if not gh_available():
        return {"ok": False, "error": "gh CLI not found in PATH"}
    try:
        r = subprocess.run(
            ["gh", "pr", "checkout", str(pr)],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
        )
        out = (r.stdout or "") + (r.stderr or "")
        return {
            "ok": r.returncode == 0,
            "exit_code": r.returncode,
            "output": out.strip()[:4000],
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
