"""Detect user write intent and curb explore-only thrash — flexible, no hard lock.

History: hard WRITE_ONLY tool strips caused 复读卡死 (model could only file_write
tiny helpers, or hit LoopGuard with no way to read). New policy:

- Soft system nudges only for pure explore loops
- Optional soft demotion of non-coding tools (web/crew), NEVER remove
  file_read / grep / command / python
- Never force_final text-only for write intent
"""

from __future__ import annotations

import re
from typing import Any

# Explicit write/create verbs only
_WRITE_RE = re.compile(
    r"(写入|写文件|写一份|写上|落盘|保存到|保存为|保存成|"
    r"生成.*(?:md|文档|spec|规格|SPEC)|"
    r"file_write|写这份|重新写|写出来|产出.*文档|"
    r"写\s*spec|写\s*PRD|写\s*(?:一份|个)?.*(?:文档|spec|SPEC|规格)|"
    r"确定.*(?:一份|个)?.*(?:spec|SPEC|规格|文档)|"
    r"整理.*(?:成|为|一份).*(?:文档|spec|SPEC|md)|"
    r"输出.*(?:文档|spec|SPEC)|起草.*(?:文档|spec|SPEC)|"
    r"做.*(?:一份|个)?.*(?:spec|SPEC|规格).*文档|"
    r"(?:做|出).*(?:一份|个)?\s*(?:spec|SPEC)(?:文档)?|"
    r"按照.*PRD.*(?:做|写|出).*(?:spec|文档)|"
    r"把\s*spec\s*写|把.*文档.*写好|写好.*(?:spec|文档)|"
    r"补全.*(?:crate|源码|代码)|file_write\s*补)",
    re.I,
)

_REVIEW_RE = re.compile(
    r"(通读|审阅|看看|看一下|看下|看有什么|有什么问题|有没有问题|"
    r"检查一下|读一下|读下|review|帮我看|去这儿看看)",
    re.I,
)

_WRITE_VERB_RE = re.compile(
    r"(写入|写文件|写一份|写上|落盘|保存|生成|产出|起草|整理成|输出|"
    r"file_write|重新写|写出来|写好|确定一份|做一份|做个|补全)",
    re.I,
)

_ATTACHMENT_MARK_RE = re.compile(r"\[附件\s*\d+\s*:", re.I)

WRITE_TOOLS = frozenset(
    {"file_write", "edit", "apply_patch", "desktop_write_file", "doc_write"}
)

# Full coding toolkit — keep these always when focusing (flexible)
CODING_FLEX_ALLOW = frozenset(
    {
        "file_write",
        "edit",
        "apply_patch",
        "desktop_write_file",
        "doc_write",
        "file_read",
        "glob",
        "grep",
        "command",
        "python",
        "manage_goal",
        "process",
        "current_time",
        "clarify",
        "doc_read",
        "result_load",
    }
)

# Only demote these on write-intent thrash (optional soft focus)
NON_CODING_TOOLS = frozenset(
    {
        "web_search",
        "search",
        "browser",
        "fetch_webpage",
        "http",
        "crew_steward",
        "delegate_task",
        "agent_call",
        "autopilot",
        "session_search",
        "shell_session",
        "image_generate",
        "tts",
    }
)

EXPLORE_TOOLS = frozenset(
    {
        "file_read",
        "glob",
        "grep",
        "command",
        "python",
        "search",
        "web_search",
        "doc_read",
        "http",
        "session_search",
        "result_load",
        "process",
        "shell_session",
        "browser",
        "fetch_webpage",
    }
)

# Legacy alias — write-only hard lock DISABLED; same as coding flex
WRITE_ONLY_ALLOW = CODING_FLEX_ALLOW


def is_write_intent(user_input: str) -> bool:
    t = (user_input or "").strip()
    if not t:
        return False
    # Prefer Rust harness authority when host is up (parity with harness_resolve)
    try:
        import os

        be = (os.environ.get("TAKTON_KERNEL_BACKEND") or "").strip().lower()
        if be not in {"python", "py", "off", "0", "none"}:
            from backend.kernel import get_kernel

            k = get_kernel()
            if hasattr(k, "harness_resolve"):
                r = k.harness_resolve(text=t)
                if isinstance(r, dict) and "write_intent" in r:
                    return bool(r.get("write_intent"))
            elif hasattr(k, "_call"):
                r = k._call("harness_resolve", {"text": t}) or {}
                if isinstance(r, dict) and "write_intent" in r:
                    return bool(r.get("write_intent"))
    except Exception:
        pass
    if not _WRITE_RE.search(t):
        return False
    if _REVIEW_RE.search(t) and not _WRITE_VERB_RE.search(t):
        return False
    return True


def attachment_text_already_in_input(
    user_input: str,
    attachments: list[dict[str, Any]] | None = None,
    *,
    min_chars: int = 400,
) -> bool:
    text = user_input or ""
    if attachments:
        for att in attachments:
            tc = att.get("text_content") if isinstance(att, dict) else None
            if isinstance(tc, str) and len(tc.strip()) >= min_chars:
                return True
    if _ATTACHMENT_MARK_RE.search(text) and len(text) >= min_chars + 80:
        return True
    return False


def write_intent_nudge_text(*, soft: bool) -> str:
    if soft:
        return (
            "[Write/coding intent] Prefer file_write/edit to disk; "
            "file_read/grep and `cargo check` are fine. "
            "Do not thrash multi-round glob-only scans — write when you have enough. "
            "Ignore `_` diagnostic junk; avoid _diag/_cargo/_reinstall scripts. "
            "Use workspace-relative paths."
        )
    return (
        "[Progress] Several explore-only rounds already. "
        "Prefer file_write/edit or `cargo check -p <crate>` next; "
        "avoid pure explore turns. Parallel writes OK. "
        "Avoid rustup/_diag and rewriting the same file in a loop."
    )


def write_intent_early_system_note(
    *,
    attachment_embedded: bool = False,
) -> str:
    parts = [
        "[Deliverable] User wants artifacts on disk (code/docs). ",
        "Tools: file_write/edit + file_read/grep/glob + command/python are all fine. ",
        "Strategy: read needed context → batch file_write → re-read/edit if needed. ",
        "Avoid thrash scans, diagnostic script loops, rustup; verify with cargo check/build. ",
    ]
    if attachment_embedded:
        parts.append(
            "[Attachment embedded] Body is already in the user message; "
            "do not repeatedly file_read the same attachment. "
        )
    return "".join(parts)


def _tool_name(t: Any) -> str:
    if not isinstance(t, dict):
        return str(getattr(t, "name", "") or "")
    fn = t.get("function") if isinstance(t.get("function"), dict) else {}
    return str((fn or {}).get("name") or t.get("name") or "")


def filter_tools_coding_flex(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Soft focus: drop web/crew noise, KEEP full coding toolkit.

    Never reduce to write-only — that caused 复读卡死
    (model lost file_read/command and spun on tiny writes / empty thrash).
    Unknown tools stay available — safer than strip-all.
    """
    if not tools:
        return tools
    out: list[dict[str, Any]] = []
    for t in tools:
        name = _tool_name(t)
        if name in NON_CODING_TOOLS:
            continue
        out.append(t)
    return out if out else tools


def filter_names_coding_flex(names: list[str] | None) -> list[str] | None:
    if names is None:
        return list(CODING_FLEX_ALLOW)
    return [n for n in names if n not in NON_CODING_TOOLS]


# Back-compat names used by tool_round / pack expand
def filter_tools_write_only(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Deprecated hard lock — now soft coding flex (no write-only strip)."""
    return filter_tools_coding_flex(tools)


def filter_names_write_only(names: list[str] | None) -> list[str] | None:
    """Deprecated — soft coding flex."""
    return filter_names_coding_flex(names)
