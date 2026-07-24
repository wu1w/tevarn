"""Heuristics to reduce hesitant single-tool rounds (efficiency, not new tools)."""
from __future__ import annotations

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


def tool_names_from_calls(tool_calls: Iterable[Any]) -> list[str]:
    names: list[str] = []
    for tc in tool_calls or []:
        n = getattr(tc, "name", None)
        if n is None and isinstance(tc, dict):
            n = (tc.get("function") or {}).get("name") or tc.get("name")
        if n:
            names.append(str(n))
    return names


def is_timid_read_round(tool_names: list[str]) -> bool:
    """True if this round only did a single read-ish tool (classic hesitation)."""
    if len(tool_names) != 1:
        return False
    return tool_names[0] in _READISH


def batch_read_nudge_text(*, consecutive_timid: int = 1) -> str:
    """System nudge after a timid single-read round."""
    base = (
        "【果断批次】你上一轮只调用了 1 个只读工具。"
        "若任务仍未完成：请在本轮一次发出多个 tool_calls——"
        "并行 file_read/grep/glob 相关文件，或在信息已够时直接 edit/file_write/command 验证。"
        "禁止再「每轮只读一个文件再停」。"
    )
    if consecutive_timid >= 2:
        base += " 已连续多轮单点试探：下一轮必须并行读取或开始修改/跑测。"
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
        "- Do not end a turn after a single successful file_read if more related files are clearly needed."
    )


__all__ = [
    "tool_names_from_calls",
    "is_timid_read_round",
    "batch_read_nudge_text",
    "decisive_coding_guidance",
]
