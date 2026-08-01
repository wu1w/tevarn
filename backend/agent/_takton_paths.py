"""Local data dirs for agent features (permissions history, auto rules, logs).

Sandbox note
------------
Job/WSL/seatbelt backends rewrite HOME / USERPROFILE to
``<workspace>/.computers/<agent>/home`` so agents cannot scribble on the host
home.  Code that needs the *real* Takton data dir must not use ``Path.home()``
alone — use ``home_dir()`` / ``host_home()`` which honor ``TAKTON_HOME`` and
``TAKTON_HOST_HOME`` injected by the computer backends.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def host_home() -> Path:
    """Real user home on the host (not the per-agent sandbox home)."""
    for key in ("TAKTON_HOST_HOME", "TAKTON_REAL_HOME"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return Path(raw).expanduser()
    # Windows: USERPROFILE may already be the sandbox home when running under
    # job_backend. Prefer USERNAME-based recovery only when clearly sandboxed.
    up = (os.environ.get("USERPROFILE") or "").strip()
    home = (os.environ.get("HOME") or "").strip()
    # Heuristic: sandbox homes live under .computers\...\home
    def _looks_sandboxed(p: str) -> bool:
        n = p.replace("/", "\\").lower()
        return ".computers" in n and n.rstrip("\\").endswith("\\home")

    if up and not _looks_sandboxed(up):
        return Path(up)
    if home and not _looks_sandboxed(home):
        return Path(home).expanduser()
    # Last resort: Path.home() (may still be sandboxed inside agent tools)
    if sys.platform == "win32":
        # Try HOMEDRIVE+HOMEPATH (job_backend usually does not override these)
        drive = (os.environ.get("HOMEDRIVE") or "").strip()
        path = (os.environ.get("HOMEPATH") or "").strip()
        if drive and path:
            candidate = Path(drive + path)
            if candidate.is_dir() and not _looks_sandboxed(str(candidate)):
                return candidate
    return Path.home()


def home_dir() -> Path:
    """Takton user data directory: ``~/.takton`` on the *host*, not sandbox home.

    Priority:
      1. TAKTON_HOME (absolute path to the .takton dir)
      2. host_home() / \".takton\"
    """
    env = (os.environ.get("TAKTON_HOME") or "").strip()
    if env:
        p = Path(env).expanduser()
    else:
        p = host_home() / ".takton"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Read-only sandbox without junction — fall back to agent-local dir
        fallback = Path.home() / ".takton"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback
        except OSError:
            return p
    return p


def ensure_sandbox_takton_link(agent_home: str | Path, real_takton: str | Path | None = None) -> Path | None:
    """Create ``agent_home/.takton`` → real host ``~/.takton`` (junction/symlink).

    Returns the link path if present, else None. Best-effort: failures are silent
    so sandbox startup never breaks.
    """
    try:
        home = Path(agent_home)
        home.mkdir(parents=True, exist_ok=True)
        target = Path(real_takton) if real_takton else home_dir()
        target.mkdir(parents=True, exist_ok=True)
        link = home / ".takton"
        if link.exists() or link.is_symlink():
            return link
        if sys.platform == "win32":
            # Directory junction does not need admin (unlike symlink)
            import subprocess

            subprocess.run(
                ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
                timeout=5,
                check=False,
            )
        else:
            link.symlink_to(target, target_is_directory=True)
        return link if (link.exists() or link.is_symlink()) else None
    except Exception:
        return None
