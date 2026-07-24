"""Heuristics to reduce hesitant single-tool rounds (efficiency, not new tools)."""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

# tools that only gather info — batching them is almost always better
_READISH = frozenset(
    {
        "file_read",
        "grep",
        "glob",
        "search",
        "web_search",
        "doc_read",
        "session_search",
        "http",
        "browser",
    }
)

# shell one-shots that are effectively "read"
_TIMID_SHELL = re.compile(
    r"^\s*(cat|head|tail|less|more|wc|ls|stat|file|nl|od|hexdump|bat|sed\s+-n)\b",
    re.I,
)


def tool_names_from_calls(tool_calls: Iterable[Any]) -> list[str]:
    names: list[str] = []
    for tc in tool_calls or []:
        n = getattr(tc, "name", None)
        if n is None and isinstance(tc, dict):
            n = (tc.get("function") or {}).get("name") or tc.get("name")
        if n:
            names.append(str(n))
    return names


def _tool_args(tc: Any) -> dict[str, Any]:
    args = getattr(tc, "arguments", None)
    if args is None and isinstance(tc, dict):
        args = (tc.get("function") or {}).get("arguments") or tc.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            return {"_raw": args}
    return args if isinstance(args, dict) else {}


def is_timid_shell_command(command: str) -> bool:
    """True if command is a single read-only shell peek (cat/ls/head/...)."""
    c = (command or "").strip()
    if not c:
        return False
    # multi-step write/build is not timid
    if any(x in c for x in ("\n", "&&", ";", "|", ">", ">>", "npm", "pip", "pytest", "python -m")):
        # allow simple pipelines of read-only: cat f | head
        if ">" in c or ">>" in c:
            return False
        if "pytest" in c or "pip " in c or "npm " in c:
            return False
        if "&&" in c or ";" in c or "\n" in c:
            return False
    return bool(_TIMID_SHELL.match(c.split("|")[0].strip()))


def is_timid_read_round(tool_names: list[str], tool_calls: Iterable[Any] | None = None) -> bool:
    """True if this round only did a single read-ish tool (classic hesitation)."""
    if len(tool_names) != 1:
        return False
    name = tool_names[0]
    if name in _READISH:
        return True
    if name == "command" and tool_calls is not None:
        calls = list(tool_calls)
        if len(calls) != 1:
            return False
        cmd = str(_tool_args(calls[0]).get("command") or "")
        return is_timid_shell_command(cmd)
    return False


def batch_read_nudge_text(*, consecutive_timid: int = 1) -> str:
    """System nudge after a timid single-read round."""
    base = (
        "【果断批次】你上一轮只做了 1 次信息收集（单文件读或 cat/ls/head）。"
        "若任务仍未完成：请在本轮一次发出多个 tool_calls——"
        "并行 file_read/grep/glob 相关文件，或信息已够时直接 edit/file_write/command 验证。"
        "禁止再「每轮只窥一眼再停」。"
    )
    if consecutive_timid >= 2:
        base += (
            " 已连续多轮单点试探：下一轮必须并行读取，"
            "或开始修改/跑测；读完可编辑内容后请立刻 edit，不要再重复 file_read 同一文件。"
        )
    return base


def decisive_coding_guidance() -> str:
    """Extra stable-layer text for coding profiles."""
    return (
        "# Decisive batching (efficiency)\n"
        "Minimize tool rounds. Default stance: batch independent work in ONE assistant turn.\n"
        "- Need several files? Emit multiple file_read/grep/glob tool_calls together.\n"
        "- Bugfix: reproduce (command) + locate (grep) + read suspects in as few rounds as possible, "
        "then edit and re-run tests in the next rounds — do not take a full turn per single read.\n"
        "- Prefer one decisive edit over many tiny exploratory reads.\n"
        "- When creating a package: write multiple files via repeated file_write in one turn when possible, "
        "then run pytest once.\n"
        "- Do not end a turn after a single successful file_read if more related files are clearly needed.\n"
        "- After you have read enough to edit, call edit/file_write in the next turn — "
        "do not keep file_read-only loops."
    )


__all__ = [
    "tool_names_from_calls",
    "is_timid_read_round",
    "is_timid_shell_command",
    "batch_read_nudge_text",
    "decisive_coding_guidance",
]
