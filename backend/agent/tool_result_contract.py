"""Normalize + budget tool results before they re-enter the LLM context.

Design (aligned with Claude Code / Codex / Cursor):

1. **Inline small results** — full text under soft per-tool budgets (tens of KB).
2. **Envelope-artifact for large results** — full body spills to kernel store;
   context gets a *rich* head+tail preview + handle id, not a 240-char stub.
3. **On-demand paging** — model calls ``result_load(id, offset, max_chars)``
   instead of re-running the original tool (re-run wastes tokens and can drift).

Never "just raise the number forever": long-doc analysis should page via
``file_read`` offset/limit or ``result_load`` slices, not stuff 200KB into one turn.
"""
from __future__ import annotations

import os as _os
import re
from typing import Any

# 按工具类型差异化截断（超出 → head+tail preview）
# 注意：file_write/edit 结果要保留完整路径与成功信息，budget 不能太狠
#
# file_read（T3）：executor 已按行边界分页并给出续读 offset，是截断的权威。
# 这里再来一刀 head+tail 拼接会把「第 1-30 行 …省略… 末尾几行」交给模型，
# 而模型以为自己读到了完整文件 —— 基于断裂视图改代码是静默错改的主因。
# audit-fix(#7)：预算 21_000 → 12_000（压 context 占用）；executor 单次分页上限
# FILE_READ_MAX_CHARS=20_000 不变，超出 12k 的读文件结果走 head+tail 兜底，
# 模型应按 executor 给出的 offset 续读而非假设已读全文。
# Aggressive defaults (H2/P1): prefer spill handle over fat LLM context.
# file_read keeps higher budget so executor pagination remains authority.
TOOL_RESULT_BUDGET: dict[str, int] = {
    "file_read": 12_000,
    "grep": 900,
    "glob": 600,
    # cargo/rustc diagnostics are long; 1200 forced head+tail thrash on E0xxx lists
    "command": 10_000,
    "file_write": 2500,  # 写入确认/回显路径；过短会导致模型重写
    "edit": 2500,
    "apply_patch": 2500,
    "python": 1000,
    "http": 800,
    "web_search": 1000,
    "search": 1000,
    "browser": 800,
    "doc_read": 1200,
    "process": 8_000,
}
DEFAULT_TOOL_BUDGET = 12_000

# Global floor: do not spill below this even if a tool budget is lower.
# Env: TEVARN_RESULT_SPILL_THRESHOLD
SPILL_THRESHOLD = int(
    _os.environ.get("TEVARN_RESULT_SPILL_THRESHOLD", "16000") or 16000
)

# Envelope preview size when spilled (head + tail). Env: TEVARN_RESULT_SPILL_PREVIEW
SPILL_PREVIEW_CHARS = int(
    _os.environ.get("TEVARN_RESULT_SPILL_PREVIEW", "8000") or 8000
)

_WRITE_TOOLS = frozenset({"file_write", "edit", "apply_patch", "desktop_write_file"})
_HANDLE_ID_RE = re.compile(
    r"tool_result_handle\s+id=([A-Za-z0-9_-]+)|use result_load id=([A-Za-z0-9_-]+)",
    re.I,
)


def is_tool_error(result: str | None) -> bool:
    """True for failures that should count toward fail-breakers / durable failed.

    Includes outer agent timeout (`[Error] Tool 'x' timed out…`) and inner
    command/python timeouts (`[Timeout] …`) — the latter used to be treated as
    success by durable run recording.

    Soft outcomes like `[Background after timeout]` are **not** errors — the
    process keeps running; model should process poll.
    """
    t = (result or "").lstrip()
    if not t:
        return False
    if t.startswith("[Background"):
        return False
    low = t[:80].lower()
    return (
        t.startswith("[Error]")
        or t.startswith("[error]")
        or t.startswith("[Timeout]")
        or t.startswith("[timeout]")
        or t.startswith("[Security")
        or t.startswith("[Denied]")
        or t.startswith("[Hook Blocked]")
        or "timed out after" in low
        or ("exceeded" in low and "terminat" in low)
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


def tool_budget(tool_name: str, *, max_chars: int | None = None) -> int:
    name = (tool_name or "").strip()
    lim = int(TOOL_RESULT_BUDGET.get(name, DEFAULT_TOOL_BUDGET))
    if name in _WRITE_TOOLS:
        lim = max(lim, 2500)
    if max_chars is not None:
        lim = int(max_chars)
        if name in _WRITE_TOOLS:
            lim = max(lim, 2500)
    return max(200, lim)


def truncate_for_llm(tool_name: str, raw_result: str, *, budget: int | None = None) -> str:
    """Head+tail truncate when spill is unavailable or result is mid-size."""
    text = raw_result or ""
    if "[Background" in text or "process_id=" in text[:200]:
        return text
    if text.startswith("[Security Blocked]") or text.startswith("[Denied]"):
        return text

    name = tool_name or ""
    if name in _WRITE_TOOLS and _is_write_ack(text) and len(text) <= 4000:
        return text

    lim = tool_budget(name) if budget is None else max(200, int(budget))
    if name in _WRITE_TOOLS:
        lim = max(lim, 2500)

    if len(text) <= lim:
        return text

    head_n = int(lim * 0.7)
    tail_n = max(120, int(lim * 0.2))
    head = text[:head_n]
    tail = text[-tail_n:]
    omitted = len(text) - len(head) - len(tail)
    return (
        f"{head}\n"
        f"...[{omitted} chars omitted for LLM context; tool={name or '?'}]...\n"
        f"{tail}\n"
        f"[hint] For full body: re-run with narrower query, or file_read with offset/limit."
    )


def _head_tail_preview(text: str, *, budget: int) -> str:
    """Rich preview for spill envelopes (Claude Code style head+tail)."""
    b = max(400, int(budget))
    if len(text) <= b:
        return text
    head_n = int(b * 0.75)
    tail_n = max(200, int(b * 0.2))
    omitted = len(text) - head_n - tail_n
    return (
        f"{text[:head_n]}\n"
        f"...[{omitted} chars omitted in preview]...\n"
        f"{text[-tail_n:]}"
    )


def _extract_handle_id(spill_payload: dict[str, Any], context: str) -> str:
    h = spill_payload.get("handle")
    if isinstance(h, dict) and h.get("id"):
        return str(h["id"])
    if isinstance(h, str) and h.strip():
        return h.strip()
    for key in ("id", "handle_id"):
        if spill_payload.get(key):
            return str(spill_payload[key])
    m = _HANDLE_ID_RE.search(context or "")
    if m:
        return (m.group(1) or m.group(2) or "").strip()
    return ""


def format_spill_envelope(
    *,
    handle_id: str,
    tool_name: str,
    full_text: str,
    bytes_hint: int | None = None,
) -> str:
    """Build model-facing envelope after spill (do not re-run the original tool)."""
    n = bytes_hint if bytes_hint is not None else len(full_text or "")
    preview = _head_tail_preview(full_text or "", budget=SPILL_PREVIEW_CHARS)
    hid = (handle_id or "").strip() or "?"
    tool = (tool_name or "tool").strip()
    searchish = any(
        x in tool.lower()
        for x in ("search", "tavily", "web_", "fetch", "scrape", "extract")
    )
    steps = (
        f"  1) result_load(id=\"{hid}\") — full text (or max_chars / offset to page)\n"
        f"  2) result_load(id=\"{hid}\", offset=0, max_chars=20000) — first page\n"
        f"  3) Do NOT re-run the same tool just to 'get full data'\n"
    )
    if searchish:
        steps += (
            f"  4) After 2+ large search spills: prefer result_load + write answer; "
            f"avoid near-duplicate web_search/mcp_*_search queries\n"
        )
    return (
        f"[tool_result_handle id={hid} tool={tool} chars={n}]\n"
        f"FULL BODY is stored externally (not truncated forever — page it).\n"
        f"NEXT STEPS (pick one):\n"
        f"{steps}"
        f"--- preview (head+tail, {SPILL_PREVIEW_CHARS} char budget) ---\n"
        f"{preview}\n"
        f"--- end preview; use result_load id={hid} for more ---"
    )


def normalize_tool_result(
    result: Any,
    *,
    max_chars: int | None = None,
    tool_name: str = "",
    process_id: str | None = None,
) -> str:
    """Coerce to str; inline if under budget; else spill envelope or head+tail."""
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

    name = (tool_name or "").strip()
    budget = tool_budget(name, max_chars=max_chars)

    # Short write acks always inline
    if name in _WRITE_TOOLS and _is_write_ack(text) and len(text) <= 4000:
        return text

    # Under soft budget → full inline (weather JSON, short analysis, etc.)
    if len(text) <= budget:
        return text

    # Over budget → try spill with *rich* Python-side envelope (ignore tiny Rust preview)
    pid = (process_id or "").strip() or "orphan"
    thr = max(int(SPILL_THRESHOLD), budget)
    if len(text) >= thr:
        try:
            from backend.kernel import get_kernel

            k = get_kernel()
            r: Any = None
            if hasattr(k, "result_spill"):
                r = k.result_spill(pid, name or "tool", text)
            elif hasattr(k, "_call"):
                r = k._call(
                    "result_spill",
                    {
                        "process_id": pid,
                        "tool": name or "tool",
                        "content": text,
                    },
                )
            if isinstance(r, dict) and r.get("spilled"):
                ctx = str(r.get("context") or "")
                hid = _extract_handle_id(r, ctx)
                if hid:
                    return format_spill_envelope(
                        handle_id=hid,
                        tool_name=name or "tool",
                        full_text=text,
                        bytes_hint=len(text),
                    )
                # spilled but no id — fall back to kernel context if present
                if ctx:
                    return ctx
        except Exception:
            pass

    # Spill unavailable / mid-size over budget → head+tail only
    return truncate_for_llm(name, text, budget=budget)


__all__ = [
    "TOOL_RESULT_BUDGET",
    "DEFAULT_TOOL_BUDGET",
    "SPILL_THRESHOLD",
    "SPILL_PREVIEW_CHARS",
    "tool_budget",
    "truncate_for_llm",
    "format_spill_envelope",
    "normalize_tool_result",
    "is_tool_error",
]
