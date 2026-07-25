"""Local data dirs for agent features (permissions history, auto rules)."""
from __future__ import annotations

import os
from pathlib import Path


def home_dir() -> Path:
    env = (os.environ.get("TAKTON_HOME") or "").strip()
    if env:
        p = Path(env).expanduser()
    else:
        p = Path.home() / ".takton"
    p.mkdir(parents=True, exist_ok=True)
    return p
