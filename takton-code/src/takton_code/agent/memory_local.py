"""Opt-in local cross-session memory (no phone-home)."""

from __future__ import annotations

import time
from pathlib import Path

from takton_code.config import home_dir


def memory_dir() -> Path:
    d = home_dir() / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


def memory_path() -> Path:
    return memory_dir() / "MEMORY.md"


def read_memory(*, max_chars: int = 6000) -> str:
    p = memory_path()
    if not p.is_file():
        return ""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) > max_chars:
        return text[: max_chars - 20] + "\n…[truncated]"
    return text


def append_memory(note: str) -> Path:
    p = memory_path()
    note = (note or "").strip()
    if not note:
        raise ValueError("empty note")
    stamp = time.strftime("%Y-%m-%d %H:%M")
    block = f"\n## {stamp}\n{note}\n"
    prev = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else "# Takton Code local memory\n"
    p.write_text(prev + block, encoding="utf-8")
    return p


def clear_memory() -> None:
    p = memory_path()
    if p.is_file():
        p.unlink()


def memory_prompt_block(*, enabled: bool) -> str:
    if not enabled:
        return ""
    body = read_memory()
    if not body.strip():
        return ""
    return f"### Local memory (user opt-in, on-disk only)\n{body}"
