"""@path and slash helpers for Claude/OpenCode-style input."""

from __future__ import annotations

import re
from pathlib import Path

# @path, @path/to/file.py, @"path with space"
_AT_RE = re.compile(
    r"""@(?:"([^"]+)"|'([^']+)'|([^\s,;:]+)?)"""
)

SLASH_COMMANDS: list[tuple[str, str]] = [
    ("/help", "Show commands"),
    ("/plan", "Plan mode (read-only)"),
    ("/build", "Build mode"),
    ("/ask", "Ask mode (read-only Q&A)"),
    ("/explore", "Explore mode (read-only search)"),
    ("/always", "Always-approve writes (Grok-style)"),
    ("/approve", "Approve plan → build"),
    ("/reject", "Reject plan"),
    ("/diff", "Show session diffs"),
    ("/undo", "Rewind last file checkpoint (EscEsc)"),
    ("/rewind", "Rewind checkpoint; files=a,b partial"),
    ("/patch", "Focus next/prev/path unified patch"),
    ("/unrewind", "Undo last rewind (redo stack)"),
    ("/hunk", "List/apply hunks: /hunk list|apply 0,2"),
    ("/checkpoint", "Create named file checkpoint"),
    ("/checkpoints", "List file checkpoints"),
    ("/autoloop", "plan→build→test→fix closed loop"),
    ("/auto-rules", "Show/reload auto permission rules"),
    ("/revert", "Revert one path"),
    ("/test", "Run tests"),
    ("/check", "Self-verify last changes"),
    ("/compress", "Force context compress"),
    ("/status", "Session status JSON"),
    ("/usage", "Token / cost usage"),
    ("/inspect", "Project + bridge + desktop ecosystem"),
    ("/continue", "Resume after interrupt"),
    ("/stop", "Cancel current turn"),
    ("/queue", "List prompt queue"),
    ("/enqueue", "Enqueue message for after turn"),
    ("/worktree", "List/status worktrees"),
    ("/model", "Shallow model switch"),
    ("/sessions", "List sessions"),
    ("/title", "Rename session"),
    ("/compact", "Compress context (alias /compress)"),
    ("/context", "Dual context meter + thrashing status"),
    ("/fork", "Fork session"),
    ("/export", "Export session json|md|jsonl"),
    ("/todo", "Show todos"),
    ("/agent", "List/select .takton/agents"),
    ("/rules", "Reload CODE/AGENTS/CLAUDE + auto_rules"),
    ("/memory", "Local memory show|on|off|add|clear"),
    ("/pr", "gh pr checkout <n|url>"),
    ("/exit", "Quit"),
]


def expand_at_refs(text: str, project_root: Path, *, max_file_chars: int = 12000) -> str:
    """Expand @file mentions into fenced file contents (best-effort)."""
    root = project_root.resolve()
    if "@" not in text:
        return text

    chunks: list[str] = []
    pos = 0
    for m in _AT_RE.finditer(text):
        if m.start() < pos:
            continue
        chunks.append(text[pos : m.start()])
        raw = m.group(1) or m.group(2) or m.group(3) or ""
        raw = raw.strip().rstrip(",.;")
        if not raw or raw.startswith("/") and raw.split()[0] in {c[0] for c in SLASH_COMMANDS}:
            chunks.append(m.group(0))
            pos = m.end()
            continue
        # skip email-like
        if "@" in raw and "." in raw and "/" not in raw and "\\" not in raw:
            chunks.append(m.group(0))
            pos = m.end()
            continue
        rel = raw.lstrip("./")
        path = (root / rel).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            chunks.append(m.group(0))
            pos = m.end()
            continue
        if not path.is_file():
            chunks.append(f"{m.group(0)}  /* missing: {rel} */")
            pos = m.end()
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            chunks.append(m.group(0))
            pos = m.end()
            continue
        if len(body) > max_file_chars:
            body = body[:max_file_chars] + "\n…[truncated]"
        lang = path.suffix.lstrip(".") or "text"
        chunks.append(f"\n\n# @{rel}\n```{lang}\n{body}\n```\n")
        pos = m.end()
    chunks.append(text[pos:])
    return "".join(chunks)


def filter_slash_commands(prefix: str) -> list[tuple[str, str]]:
    p = (prefix or "/").lower()
    if not p.startswith("/"):
        p = "/" + p
    hits = [(c, d) for c, d in SLASH_COMMANDS if c.startswith(p)]
    if hits:
        return hits
    # fuzzy contains
    key = p.lstrip("/")
    return [(c, d) for c, d in SLASH_COMMANDS if key in c.lstrip("/")][:12]


def cycle_permission_mode(current: str) -> str:
    """Grok-style: build → plan → always → build. ask/explore stay until cycled from build."""
    order = ["build", "plan", "always"]
    cur = current if current in order else "build"
    try:
        i = order.index(cur)
    except ValueError:
        i = 0
    return order[(i + 1) % len(order)]
