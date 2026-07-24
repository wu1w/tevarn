"""Normalize + budget-truncate tool results before they re-enter the LLM context.

Inspired by Claude Code toolResultStorage (deterministic truncation, no Haiku).
"""
from __future__ import annotations

from typing import Any

# 按工具类型差异化截断（超出 → head+tail preview）
TOOL_RESULT_BUDGET: dict[str, int] = {
    "file_read": 2000,
    "grep": 1500,
    "glob": 800,
    "command": 3000,
    "file_write": 400,
    "edit": 400,
    "apply_patch": 400,
    "python": 2000,
    "http": 1000,
    "web_search": 1500,
    "search": 1500,
    "browser": 1000,
    "doc_read": 2000,
    "process": 1500,
}
DEFAULT_TOOL_BUDGET = 1000


def is_tool_error(result: str | None) -> bool:
    t = (result or "").lstrip()
    return (
        t.startswith("[Error]")
        or t.startswith("[error]")
        or t.startswith("[Security")
        or t.startswith("[Denied]")
        or t.startswith("[Hook Blocked]")
    )


def truncate_for_llm(tool_name: str, raw_result: str, *, budget: int | None = None) -> str:
    """按工具类型截断 result，只把摘要塞给 LLM。"""
    text = raw_result or ""
    # 后台任务提示不截断
    if "[Background" in text or "process_id=" in text[:200]:
        return text
    if text.startswith("[Security Blocked]") or text.startswith("[Denied]"):
        return text

    lim = int(budget if budget is not None else TOOL_RESULT_BUDGET.get(tool_name or "", DEFAULT_TOOL_BUDGET))
    lim = max(200, lim)
    if len(text) <= lim:
        return text

    head_n = int(lim * 0.7)
    tail_n = max(80, int(lim * 0.2))
    head = text[:head_n]
    tail = text[-tail_n:]
    omitted = len(text) - len(head) - len(tail)
    return (
        f"{head}\n"
        f"...[{omitted} chars omitted for LLM context; tool={tool_name or '?'}]...\n"
        f"{tail}"
    )


def normalize_tool_result(
    result: Any,
    *,
    max_chars: int | None = None,
    tool_name: str = "",
) -> str:
    """Coerce to str, apply per-tool budget (or max_chars override)."""
    if result is None:
        text = ""
    elif isinstance(result, str):
        text = result
    elif isinstance(result, dict):
        import json

        # avoid huge nested dumps
        try:
            text = json.dumps(result, ensure_ascii=False, default=str)
        except Exception:
            text = str(result)
    else:
        try:
            text = str(result)
        except Exception:
            text = f"[Error] tool {tool_name or '?'} returned non-string unprintable result"

    if not text:
        text = f"[Error] Tool '{tool_name or '?'}' returned empty result"

    # max_chars 显式传入时作为硬上限；否则按工具预算
    if max_chars is not None:
        return truncate_for_llm(tool_name, text, budget=int(max_chars))
    return truncate_for_llm(tool_name, text)


__all__ = [
    "TOOL_RESULT_BUDGET",
    "DEFAULT_TOOL_BUDGET",
    "truncate_for_llm",
    "normalize_tool_result",
    "is_tool_error",
]
