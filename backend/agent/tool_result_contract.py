"""Normalize + budget-truncate tool results before they re-enter the LLM context.

Inspired by Claude Code toolResultStorage (deterministic truncation, no Haiku).
"""
from __future__ import annotations

from typing import Any

# 按工具类型差异化截断（超出 → head+tail preview）
# 注意：file_write/edit 结果要保留完整路径与成功信息，budget 不能太狠
#
# file_read（T3）：executor 已按行边界分页并给出续读 offset，是截断的权威。
# 这里再来一刀 head+tail 拼接会把「第 1-30 行 …省略… 末尾几行」交给模型，
# 而模型以为自己读到了完整文件 —— 基于断裂视图改代码是静默错改的主因。
# 故 budget 抬到与 executor 的 FILE_READ_MAX_CHARS 同量级，让分页成为唯一截断点；
# 只有病态超长行才会兜底走 head+tail。
# Aggressive defaults (H2/P1): prefer spill handle over fat LLM context.
# file_read keeps higher budget so executor pagination remains authority.
TOOL_RESULT_BUDGET: dict[str, int] = {
    "file_read": 21_000,
    "grep": 900,
    "glob": 600,
    "command": 1200,
    "file_write": 2500,  # 写入确认/回显路径；过短会导致模型重写
    "edit": 2500,
    "apply_patch": 2500,
    "python": 1000,
    "http": 800,
    "web_search": 1000,
    "search": 1000,
    "browser": 800,
    "doc_read": 1200,
    "process": 900,
}
DEFAULT_TOOL_BUDGET = 800

# Spill to kernel result store at/above this size (chars). Env override.
import os as _os

SPILL_THRESHOLD = int(
    _os.environ.get("TAKTON_RESULT_SPILL_THRESHOLD", "800") or 800
)

# 写类工具：成功短回执几乎不截断
_WRITE_TOOLS = frozenset({"file_write", "edit", "apply_patch", "desktop_write_file"})


def is_tool_error(result: str | None) -> bool:
    t = (result or "").lstrip()
    return (
        t.startswith("[Error]")
        or t.startswith("[error]")
        or t.startswith("[Security")
        or t.startswith("[Denied]")
        or t.startswith("[Hook Blocked]")
    )


def _is_write_ack(text: str) -> bool:
    t = text.lstrip()
    return (
        t.startswith("[Success]")
        or t.startswith("Success")
        or "written" in t[:80].lower()
        or "写入" in t[:40]
        or "已写入" in t[:40]
    )


def truncate_for_llm(tool_name: str, raw_result: str, *, budget: int | None = None) -> str:
    """按工具类型截断 result，只把摘要塞给 LLM。"""
    text = raw_result or ""
    # 后台任务提示不截断
    if "[Background" in text or "process_id=" in text[:200]:
        return text
    if text.startswith("[Security Blocked]") or text.startswith("[Denied]"):
        return text

    name = tool_name or ""
    # 写成功短回执：完整保留（避免丢路径导致重写）
    if name in _WRITE_TOOLS and _is_write_ack(text) and len(text) <= 4000:
        return text

    lim = int(
        budget
        if budget is not None
        else TOOL_RESULT_BUDGET.get(name, DEFAULT_TOOL_BUDGET)
    )
    lim = max(200, lim)
    if name in _WRITE_TOOLS:
        lim = max(lim, 2500)

    if len(text) <= lim:
        return text

    head_n = int(lim * 0.7)
    tail_n = max(80, int(lim * 0.2))
    head = text[:head_n]
    tail = text[-tail_n:]
    omitted = len(text) - len(head) - len(tail)
    return (
        f"{head}\n"
        f"...[{omitted} chars omitted for LLM context; tool={name or '?'}]...\n"
        f"{tail}"
    )


def normalize_tool_result(
    result: Any,
    *,
    max_chars: int | None = None,
    tool_name: str = "",
    process_id: str | None = None,
) -> str:
    """Coerce to str, apply per-tool budget (or max_chars override).

    P0.5：超大结果优先经 Rust result_spill 外置，上下文只留句柄。
    """
    if result is None:
        text = ""
    elif isinstance(result, str):
        text = result
    elif isinstance(result, dict):
        import json

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

    # Aggressive spill: kernel store keeps full body; LLM sees handle+preview.
    pid = (process_id or "").strip() or "orphan"
    thr = int(SPILL_THRESHOLD)
    # write acks stay inline if short
    if not (
        (tool_name or "") in _WRITE_TOOLS
        and _is_write_ack(text)
        and len(text) <= 4000
    ):
        if len(text) >= thr:
            try:
                from backend.kernel import get_kernel

                k = get_kernel()
                if hasattr(k, "result_spill"):
                    r = k.result_spill(pid, tool_name or "tool", text)
                elif hasattr(k, "_call"):
                    r = k._call(
                        "result_spill",
                        {
                            "process_id": pid,
                            "tool": tool_name or "tool",
                            "content": text,
                        },
                    )
                else:
                    r = None
                if isinstance(r, dict) and r.get("spilled") and r.get("context"):
                    return str(r["context"])
            except Exception:
                pass

    if max_chars is not None:
        # 写工具：max_chars 不得压到 2500 以下
        if (tool_name or "") in _WRITE_TOOLS:
            max_chars = max(int(max_chars), 2500)
        return truncate_for_llm(tool_name, text, budget=int(max_chars))
    return truncate_for_llm(tool_name, text)


__all__ = [
    "TOOL_RESULT_BUDGET",
    "DEFAULT_TOOL_BUDGET",
    "SPILL_THRESHOLD",
    "truncate_for_llm",
    "normalize_tool_result",
    "is_tool_error",
]
