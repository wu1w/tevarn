"""Heuristics to reduce hesitant single-tool rounds (efficiency, not new tools)."""
from __future__ import annotations

import json
from typing import Any, Iterable

from backend.agent.command_classifier import classify_command

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
    """True if command is read-only peek (cat/ls/head/git status/...)."""
    return classify_command(command) == "read"


_WRITEISH = frozenset({"file_write", "edit", "apply_patch"})


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


def is_timid_write_round(tool_names: list[str]) -> bool:
    """单轮只写一个文件——建包场景应并行多 file_write。"""
    return len(tool_names) == 1 and tool_names[0] in _WRITEISH


def batch_read_nudge_text(*, consecutive_timid: int = 1) -> str:
    """System nudge after a timid single-read round."""
    base = (
        "【果断批次】你上一轮只做了 1 次信息收集（单文件读或只读 shell）。"
        "若任务仍未完成：请在本轮一次发出多个 tool_calls——"
        "并行 file_read/grep/glob 相关文件，或信息已够时直接 edit/file_write/command 验证。"
        "禁止再「每轮只窥一眼再停」。"
    )
    if consecutive_timid >= 2:
        base += (
            " 已连续多轮单点试探：下一轮必须并行读取，"
            "或开始修改/跑测；读完可编辑内容后请立刻 edit，不要再重复 file_read 同一文件。"
        )
    if consecutive_timid >= 3:
        base += (
            " CRITICAL: 你已连续 3+ 轮只调 1 个工具。"
            "请立即并行多个 tool_calls；若信息足够，直接 edit/file_write，停止继续只读。"
        )
    return base


def batch_write_nudge_text(*, consecutive_timid: int = 1) -> str:
    """建包/多文件场景：单次 file_write 后催并行写。"""
    base = (
        "【建包批次写】你上一轮只 file_write/edit 了一个文件。"
        "若仍在搭建包/多文件骨架：请在本轮一次发出多个 file_write（__init__.py、模块、tests、pyproject 等），"
        "写齐后再 command 跑一次 pytest。不要一文件一轮。"
    )
    if consecutive_timid >= 2:
        base += " 已连续单文件写入：下一轮必须并行多个 file_write 或直接跑测收官。"
    return base


def decisive_coding_guidance() -> str:
    """Extra stable-layer text for coding profiles."""
    return (
        "# Decisive batching (efficiency)\n"
        "Minimize tool rounds. Default stance: batch independent work in ONE assistant turn.\n"
        "- Need several files? Emit multiple file_read/grep/glob tool_calls together.\n"
        "- Bugfix: reproduce (command) + locate (grep) + read suspects in as few rounds as possible, "
        "then edit and re-run tests — do not take a full turn per single read.\n"
        "- Prefer one decisive edit over many tiny exploratory reads.\n"
        "- When creating a package / scaffolding: HARD RULE — emit ALL planned file_write "
        "calls in ONE assistant turn (__init__.py, modules, tests, configs), then ONE pytest. "
        "Never write a single source file per turn when the file list is already known.\n"
        "- When fixing a bug and the path is known: read + run tests can be same-turn if independent "
        "of each other after the fix; after read, next turn should edit.\n"
        "- Do not end a turn after a single successful file_read if more related files are clearly needed.\n"
        "- After you have read enough to edit, call edit/file_write next — no more file_read-only loops."
    )


__all__ = [
    "tool_names_from_calls",
    "is_timid_read_round",
    "is_timid_write_round",
    "is_timid_shell_command",
    "batch_read_nudge_text",
    "batch_write_nudge_text",
    "decisive_coding_guidance",
]
