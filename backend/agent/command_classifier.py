"""Shell command semantic classification (read / write / mixed / unknown).

Rule-based (not AST) — good enough for decisive nudges and thrash signals.
"""
from __future__ import annotations

import re

READ_COMMANDS = frozenset(
    {
        "cat",
        "head",
        "tail",
        "less",
        "more",
        "wc",
        "stat",
        "file",
        "strings",
        "jq",
        "awk",
        "cut",
        "sort",
        "uniq",
        "tr",
        "nl",
        "od",
        "hexdump",
        "ls",
        "tree",
        "du",
        "df",
        "find",
        "locate",
        "which",
        "whereis",
        "grep",
        "rg",
        "ag",
        "ack",
        "diff",
        "comm",
        "echo",
        "printf",
        "true",
        "false",
        "pwd",
        "hostname",
        "date",
        "env",
        "printenv",
        "type",
        "basename",
        "dirname",
    }
)

WRITE_COMMANDS = frozenset(
    {
        "rm",
        "mv",
        "cp",
        "mkdir",
        "rmdir",
        "chmod",
        "chown",
        "touch",
        "ln",
        "tar",
        "zip",
        "unzip",
        "dd",
        "tee",
        "sed",
        "install",
    }
)

REDIRECT_PATTERN = re.compile(r"(?:^|[^0-9])(?:[12]?>{1,2}|>>)")
PIPE_PATTERN = re.compile(r"\|")


def classify_command(command: str) -> str:
    """Return 'read' / 'write' / 'mixed' / 'unknown'."""
    cmd = (command or "").strip()
    if not cmd:
        return "unknown"

    # multi-step
    segments = re.split(r"&&|;|\|\|", cmd)
    classifications: set[str] = set()
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        classifications.add(_classify_single(seg))

    if not classifications:
        return "unknown"
    if classifications == {"read"}:
        return "read"
    if classifications == {"write"}:
        return "write"
    if "write" in classifications and "read" in classifications:
        return "mixed"
    if "write" in classifications:
        return "write"
    if "read" in classifications and "unknown" in classifications:
        return "mixed"
    if len(classifications) == 1:
        return next(iter(classifications))
    return "mixed"


def _classify_single(seg: str) -> str:
    s = seg.strip()
    if not s:
        return "unknown"

    # redirects to file → write (stdout to file)
    if REDIRECT_PATTERN.search(s) or "<<" in s:
        return "write"

    # pipeline: classify each stage; any write wins
    if PIPE_PATTERN.search(s):
        parts = [p.strip() for p in s.split("|") if p.strip()]
        classes = {_classify_single_no_pipe(p) for p in parts}
        if "write" in classes:
            return "write"
        if classes == {"read"}:
            return "read"
        return "unknown"

    return _classify_single_no_pipe(s)


def _classify_single_no_pipe(seg: str) -> str:
    words = seg.split()
    if not words:
        return "unknown"
    cmd_name = words[0].split("/")[-1]

    # python / node one-liners often read-only; pytest read-ish (doesn't mutate src)
    if cmd_name in ("python", "python3", "py"):
        joined = " ".join(words[:4])
        if "-m" in words and "pytest" in words:
            return "read"
        if "-c" in words:
            return "read"
        if any(w.endswith(".py") for w in words[1:3]) and not any(
            x in seg for x in (">", "open(", "write")
        ):
            return "read"
        return "unknown"

    if cmd_name in ("node", "nodejs") and "-e" in words:
        return "read"

    if cmd_name == "git" and len(words) > 1:
        sub = words[1]
        if sub in ("log", "show", "status", "diff", "blame", "branch", "tag", "remote", "rev-parse"):
            return "read"
        if sub in ("add", "commit", "push", "reset", "checkout", "merge", "rebase", "stash", "clean"):
            return "write"
        return "unknown"

    if cmd_name in ("pip", "pip3", "npm", "pnpm", "yarn", "cargo", "uv"):
        if len(words) > 1 and words[1] in ("install", "uninstall", "add", "remove", "i"):
            return "write"
        if len(words) > 1 and words[1] in ("list", "show", "view", "outdated", "test", "run"):
            return "read"
        return "unknown"

    if cmd_name == "pytest" or cmd_name == "py.test":
        return "read"

    if cmd_name in READ_COMMANDS:
        return "read"
    if cmd_name in WRITE_COMMANDS:
        return "write"
    return "unknown"


__all__ = ["classify_command", "READ_COMMANDS", "WRITE_COMMANDS"]
