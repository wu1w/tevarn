"""Progress guard: stop read thrash, force deliver, workspace anchor, goal cadence.

Aligned with industry patterns (Claude Code budgets/hooks, Codex apply_patch discipline,
OpenHands stuck detector, SWE-agent ACI):
- file_read cap → deliver-only tool set
- cargo compile fail → force write paths (not text force_final)
- pure-read / no-write streak → deliver + shell lock
- deliver_mode: command only cargo/git (no dump/where/Get-Content thrash)
- same-path re-read cap; diag write does not count as progress
"""

from __future__ import annotations

import os
import re
from typing import Any

# After file_read LoopGuard / cap: only these tools stay visible
DELIVER_TOOL_ALLOW = frozenset(
    {
        "file_write",
        "edit",
        "apply_patch",
        "desktop_write_file",
        "doc_write",
        "command",  # further restricted by is_deliver_allowed_command
        "manage_goal",
        "process",
        "result_load",
        "clarify",
        "current_time",
        "grep",  # error-line search only; glob/file_read stripped
        # python stripped in deliver — used for dump thrash
    }
)

SCAN_TOOLS = frozenset(
    {
        "file_read",
        "doc_read",
        "glob",
        "browser",
        "fetch_webpage",
        "web_search",
        "search",
        "session_search",
        "crew_steward",
        "delegate_task",
        "agent_call",
    }
)

WRITE_TOOLS = frozenset(
    {"file_write", "edit", "apply_patch", "desktop_write_file", "doc_write"}
)

READ_ONLY_TOOLS = frozenset({
    "file_read", "doc_read", "glob", "grep", "result_load",
    "web_search", "search", "current_time", "session_search",
    "list_available_models", "get_system_status", "capability_status", "fetch_webpage",
})

_DIAG_NAME_RE = re.compile(
    r"(?i)(?:^|[/\\])_(?:cargo|diag|reinstall|hello|t_msvc|check|run_|gen_|find_|clean_)"
)
_HANDLE_RE = re.compile(
    r"\[tool_result_handle\s+id=([a-fA-F0-9]+)[^\]]*\]",
    re.I,
)
_FILE_READ_CAP_RE = re.compile(
    r"(?i)file_read\s*次数已达上限|max_file_reads|file_read.*cap",
)
_METADATA_STUB_RE = re.compile(
    r"(?i)only metadata stub found|metadata stub|Missing manifest in toolchain",
)
_CARGO_CMD_RE = re.compile(
    r"(?i)\bcargo(?:\.exe)?\s+(?:check|build|test|clippy|metadata)\b"
)
_CARGO_ERR_RE = re.compile(
    r"(?i)error\[E\d+\]|could not compile|error: could not compile|"
    r"aborting due to|compilation failed|exit.?code.?101"
)
# rustc: --> path\to\file.rs:line:col
_CARGO_PATH_RE = re.compile(
    r"(?m)^\s*-->\s+([^\s:]+\.(?:rs|toml))(?::\d+)?",
)
_SHELL_PROBE_RE = re.compile(
    r"(?i)^\s*(?:where|dir|ls|Get-ChildItem|Get-Content|Get-Command|type|cat|findstr|"
    r"Select-Object|Write-Host|echo)\b"
)
_SHELL_DUMP_RE = re.compile(
    r"(?i)(Get-Content|type|cat|Select-String|Out-File|_snap|dump\.ps1|"
    r"Split-Path|Set-Content.*_snap)"
)
_DELIVER_CMD_ALLOW_RE = re.compile(
    r"(?i)\b("
    r"cargo(?:\.exe)?\s+(?:check|build|test|clippy|metadata|clean|fmt|tree)|"
    r"git(?:\.exe)?\s+(?:status|diff|log|show|branch|rev-parse)|"
    r"rustc(?:\.exe)?\s+-vV|"
    r"cargo(?:\.exe)?\s+-V"
    r")\b"
)
_SNAP_WRITE_RE = re.compile(
    r"(?i)(?:^|[/\\])_(?:snap|cargo|diag|reinstall|hello|check|run_|gen_|find_|clean_)"
    r"|_snap[/\\]|dump\.ps1|/tmp/|_tmp"
)


def _tool_name(t: Any) -> str:
    if not isinstance(t, dict):
        return str(getattr(t, "name", "") or "")
    fn = t.get("function") if isinstance(t.get("function"), dict) else {}
    return str((fn or {}).get("name") or t.get("name") or "")


def is_diag_junk_path(path: str) -> bool:
    """True for _snap/_diag/_cargo dumps — block read AND write."""
    p = (path or "").replace("\\", "/")
    if _SNAP_WRITE_RE.search(p):
        return True
    base = os.path.basename(p)
    if "/_snap/" in f"/{p}/" or p.startswith("_snap/") or "/_tmp/" in f"/{p}/":
        return True
    # Underscore + diagnostic-ish basename (review/list/tmp probes from thrash runs)
    if base.startswith("_") and (
        base.startswith("_cargo")
        or base.startswith("_diag")
        or base.startswith("_snap")
        or base.startswith("_reinstall")
        or base.startswith("_hello")
        or base.startswith("_t")
        or base.startswith("_check")
        or base.startswith("_run_")
        or base.startswith("_m0_")
        or base.startswith("_gen_")
        or base.startswith("_find_")
        or base.startswith("_clean_")
        or base.startswith("_tmp")
        or base.startswith("_list")
        or base.startswith("_review")
        or base.startswith("_probe")
        or base.startswith("_scan")
        or base.startswith("_file")
        or base.startswith("_rs_")
        or base.endswith(".log")
        or base.endswith(".bat")
        or base.endswith(".ps1")
        or base.endswith(".cmd")
        or (
            base.endswith((".py", ".txt", ".json"))
            and any(
                k in base
                for k in (
                    "cargo",
                    "diag",
                    "msvc",
                    "dump",
                    "review",
                    "list",
                    "tmp",
                    "probe",
                    "index",
                    "manifest",
                    "filelist",
                    "scan",
                    "paths",
                )
            )
        )
    ):
        return True
    return bool(_DIAG_NAME_RE.search(p))


# Whole-file grep thrash (deliver mode): patterns that dump entire files
_WHOLE_FILE_GREP_RE = re.compile(
    r"(?x)^\s*(?:"
    r"\.\*"  # .*
    r"|\.\+"  # .+
    r"|\[\s*\\s\\S\s*\]\s*[\*\+]?"  # [\s\S]* / [\s\S]+
    r"|\(\?s\)\.\*"  # (?s).*
    r"|\[\^\]\s*\*"  # [^]*
    r"|\^\$?"  # ^ or ^$
    r"|\$"  # $
    r"|\."  # single-dot
    r")\s*$"
)
_REVIEW_ONLY_RE = re.compile(
    r"(?i)(代码审查|code\s*review|复查|通读|质量堪忧|源码质量|findings|"
    r"review\s+(the\s+)?(code|source|源码)|看看.*源码|读一遍|只读审查)"
)
_FIX_TASK_RE = re.compile(
    r"(?i)(修编译|对齐编译|cargo\s*check|error\[E|E\d{3,4}|强制改|"
    r"实现|写入|fix\s+(the\s+)?(compile|build|error)|修错|改代码)"
)
_CARGO_PATH_ENV_RE = re.compile(
    r"(?i)failed to load manifest|could not find [`']?Cargo\.toml[`']?|"
    r"manifest path .* does not exist|no such file or directory|"
    r"is not a member of (?:the )?workspace|"
    r"current package believes it's in a workspace|"
    r"could not find `Cargo\.toml`|"
    r"cwd does not exist|does not exist:\s*.*(?:crates|Cargo\.toml)"
)
_CARGO_LINKER_ENV_RE = re.compile(
    r"(?i)link\.exe|LNK\d{4}|linker [`']?\w+[`']? not found|"
    r"unable to find utility.*link|msvc.*not (?:found|installed)"
)
_CARGO_SOURCE_ERR_RE = re.compile(
    r"(?i)error\[E\d+\]|"
    r"-->\s+\S+\.rs:\d+|"
    r"could not compile(?:\s+`[^`]+`)?|"
    r"error:\s+could not compile|"
    r"aborting due to \d+ previous error"
)


def classify_grep_pattern(pattern: str) -> str:
    """Return 'whole_file' | 'narrow' | 'empty' for deliver grep policy."""
    raw = (pattern or "").strip()
    if not raw:
        return "empty"
    # strip common inline flags wrappers lightly
    p = raw
    if _WHOLE_FILE_GREP_RE.match(p):
        return "whole_file"
    # pure metachar thrash (no alnum token of len>=2)
    if not re.search(r"[A-Za-z0-9_\u4e00-\u9fff]{2,}", p):
        return "whole_file"
    return "narrow"


def is_deliver_allowed_grep(pattern: str, path: str = "") -> bool:
    """In deliver/cargo_fix: only narrow error-line / symbol greps.

    Blocks .* / ^ / [\\s\\S] whole-file dumps used as fake file_read.
    """
    try:
        from backend.core.config import settings as _st

        if not bool(getattr(_st, "agent_deliver_block_whole_file_grep", True)):
            return True
    except Exception:
        pass
    kind = classify_grep_pattern(pattern)
    if kind != "narrow":
        return False
    # optional: refuse recursive workspace-root dumps with broad path
    _ = path  # reserved for future path-scoped policy
    return True


def is_review_only_task(user_input: str) -> bool:
    """True when user wants review/audit, not compile-fix coding."""
    text = (user_input or "").strip()
    if not text:
        return False
    if _FIX_TASK_RE.search(text):
        return False
    try:
        from backend.agent.task_grounding import is_audit_like_task

        if is_audit_like_task(text):
            return True
    except Exception:
        pass
    return bool(_REVIEW_ONLY_RE.search(text))


def should_arm_deliver_mode(user_input: str, *, reason: str = "") -> bool:
    """Whether to strip scan tools into deliver-only mode.

    Always True for cargo_fix / shell thrash reasons.
    For pure-read / file_read-cap / no-write: skip when user asked for review-only
    so code review is not starved of file_read.
    """
    r = (reason or "").strip().lower()
    if r in ("cargo_fix", "cargo_verify", "shell_probe", "compile_source"):
        return True
    try:
        from backend.core.config import settings as _st

        if not bool(getattr(_st, "agent_deliver_skip_for_audit", True)):
            return True
    except Exception:
        pass
    if r in ("pure_read", "file_read_cap", "no_write", "max_file_reads"):
        if is_review_only_task(user_input):
            return False
    return True


def classify_cargo_error(result: str) -> str:
    """Classify cargo failure for arming policy.

    Returns: compile_source | path_env | toolchain | linker_env | unknown | ok
    Only compile_source should set must_write_before_cargo.
    """
    t = result or ""
    if not t.strip():
        return "unknown"
    if is_metadata_stub_error(t):
        return "toolchain"
    if _CARGO_LINKER_ENV_RE.search(t):
        return "linker_env"
    if _CARGO_PATH_ENV_RE.search(t):
        return "path_env"
    # Strong source signals first
    if re.search(r"(?i)error\[E\d+\]", t) or _CARGO_PATH_RE.search(t):
        return "compile_source"
    if _CARGO_SOURCE_ERR_RE.search(t) and not _CARGO_PATH_ENV_RE.search(t):
        return "compile_source"
    if re.search(r"(?i)Finished|status=done\s+exit=0|\[Exit\s+0\b", t) and not re.search(
        r"(?i)error\[", t
    ):
        return "ok"
    # Exit 101 alone (no E-code / --> path / could not compile) → unknown
    # (covers truncated bg output; do NOT arm write-gate on bare 101)
    if re.search(r"(?i)(?:status=done\s+exit=101|\[Exit\s+101\b|exit.?code.?101)", t):
        return "unknown"
    return "unknown"


def is_probe_overwrite(path: str, content: str) -> bool:
    """True when write looks like a thrash probe clobbering business source.

    Conservative: only small bodies on lib/main/mod.rs without real type/fn defs,
    or explicit probe markers. Does not block normal multi-hunk rewrites.
    """
    try:
        from backend.core.config import settings as _st

        if not bool(getattr(_st, "agent_block_probe_overwrite", True)):
            return False
    except Exception:
        pass
    p = (path or "").replace("\\", "/")
    if not p.endswith(".rs"):
        return False
    body = content or ""
    if len(body) > 900:
        return False
    if re.search(
        r"(?i)\b(probe|placeholder|TODO:\s*read|Get-Content|print!\s*\(\s*[\"']hello)",
        body,
    ):
        return True
    base = os.path.basename(p)
    if base in ("lib.rs", "main.rs", "mod.rs") and len(body) < 320:
        if not re.search(
            r"\b(struct|enum|impl|trait|async\s+fn|pub\s+(?:struct|fn|enum|mod)|mod\s+\w+)",
            body,
        ):
            return True
    return False


def path_env_cargo_nudge(workspace_root: str = "") -> str:
    """Soft nudge when cargo failed due to wrong cwd / missing manifest — not write-gate."""
    proj = resolve_project_anchor(workspace_root) or workspace_root or "(project)"
    return (
        f"[Cargo path/workspace] Not a source E0xxx; project anchor: `{proj}`\n"
        + next_action_menu("path_env")
    )


def doom_loop_handoff(
    *,
    deliver_mode: bool = False,
    must_write: bool = False,
    cargo_paths: str = "",
    cargo_class: str = "",
    last_tools: str = "",
    workspace_root: str = "",
) -> str:
    """Short, useful doom handoff — no long scare inventory."""
    try:
        from backend.core.config import settings as _st

        if not bool(getattr(_st, "agent_doom_handoff_enrich", True)):
            return (
                "[Tool thrash trip] Same tool+args repeated; tools stopped. "
                "Summarize from existing results in the user's language; "
                "next: change parameters/tools."
            )
    except Exception:
        pass
    anchor = resolve_project_anchor(workspace_root) or workspace_root or ""
    lines = [
        "[Tool thrash trip] Same tool+args repeated; tools stopped. "
        "Answer from existing results only (user language).",
    ]
    if anchor:
        lines.append(f"Project anchor: `{anchor}` (run cargo under this cwd).")
    if cargo_class == "path_env":
        lines.append(
            "Recent cargo looks like path/workspace issues: cd to the anchor, "
            "then check — do not randomly rewrite sources."
        )
    elif must_write or cargo_class == "compile_source":
        if cargo_paths:
            lines.append(f"Compile paths to fix: {cargo_paths}")
        lines.append(
            "Next: file_write/edit those paths, then cargo check; "
            "do not retry identical tools or scan the disk."
        )
    else:
        lines.append(
            "Next: change tools/args; use file_write/edit for product changes; "
            "avoid whole-file grep and `_` diagnostic scripts."
        )
    if last_tools:
        lines.append(f"Recent tools: {last_tools[:100]}")
    if deliver_mode and not must_write:
        lines.append("(This run was in deliver mode: prefer write/check over scanning.)")
    return "\n".join(lines)


# 本轮 run 级 soft_open 覆盖（loop 在非 goal 时设为 False）
_soft_open_run: bool | None = None


def set_soft_open_for_run(enabled: bool | None) -> None:
    """loop 入口设置：None=跟随全局配置；False=本轮硬闸门。"""
    global _soft_open_run
    _soft_open_run = enabled


def soft_open_mode() -> bool:
    """True: no hard tool walls (deliver/must_write/thrash force_final).

    全局 ``agent_soft_open_mode`` 默认 True；若 ``agent_soft_open_goal_only``
    且本轮非 goal，loop 会 set_soft_open_for_run(False)，使非 goal 硬停 thrash。
    """
    global _soft_open_run
    if _soft_open_run is not None:
        return bool(_soft_open_run)
    try:
        from backend.core.config import settings as _st

        return bool(getattr(_st, "agent_soft_open_mode", True))
    except Exception:
        return True


def converge_nudge_text(*, tool_rounds: int) -> str:
    """Soft wrap-up reminder when the run has many tool rounds."""
    return (
        f"[Converge] ~{int(tool_rounds)} tool rounds this segment. "
        "Prefer turning work into verifiable results "
        "(write → cargo check/tests → update goal). "
        "Avoid repeated disk scans / empty polls. "
        "You may still use any tool — this is not a ban."
    )


def filter_tools_deliver_only(
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    if soft_open_mode():
        return tools
    if not tools:
        return tools
    out = [t for t in tools if _tool_name(t) in DELIVER_TOOL_ALLOW]
    return out if out else tools


def filter_names_deliver_only(names: list[str] | None) -> list[str] | None:
    if soft_open_mode():
        return names
    if names is None:
        return list(DELIVER_TOOL_ALLOW)
    return [n for n in names if n in DELIVER_TOOL_ALLOW]


def extract_result_handle(text: str) -> str:
    m = _HANDLE_RE.search(text or "")
    return m.group(1) if m else ""


def is_file_read_cap_message(text: str) -> bool:
    return bool(_FILE_READ_CAP_RE.search(text or ""))


def is_metadata_stub_error(text: str) -> bool:
    return bool(_METADATA_STUB_RE.search(text or ""))


def next_action_menu(kind: str, **ctx: Any) -> str:
    """3-option NEXT menu appended to Blocked / throttle / nudges."""
    try:
        from backend.core.config import settings as _st

        if not bool(getattr(_st, "agent_next_action_menus", True)):
            return ""
    except Exception:
        pass
    paths = str(ctx.get("paths") or ctx.get("cargo_paths") or "").strip()
    path_hint = paths.split(",")[0].strip() if paths else "<path from --> in error>"
    menus = {
        "must_write_blocks_cargo": (
            "NEXT:\n"
            f"1) file_write/edit `{path_hint}` for error[E…]\n"
            "2) manage_goal: note the blocker\n"
            "3) Avoid pure re-check / spam poll / whole-file grep"
        ),
        "deliver_blocks_read": (
            "NEXT:\n"
            "1) file_write/edit known error files\n"
            "2) result_load to page large prior output\n"
            "3) Avoid file_read/glob/whole-file grep"
        ),
        "deliver_blocks_shell": (
            "NEXT:\n"
            "1) Only cargo check/build/test or git status/diff\n"
            "2) file_write/edit product .rs\n"
            "3) Avoid Get-Content/dir/where/_snap"
        ),
        "whole_file_grep": (
            "NEXT:\n"
            "1) Narrow pattern (error[E…] / symbol name)\n"
            "2) file_write/edit known paths\n"
            "3) Avoid .* /^ /[\\s\\S] whole-file dumps"
        ),
        "junk_write": (
            "NEXT:\n"
            "1) Write product sources under crates/**/*.rs only\n"
            "2) edit compile-error paths\n"
            "3) Avoid _tmp/_diag/_review diagnostic files"
        ),
        "probe_overwrite": (
            "NEXT:\n"
            "1) edit/apply_patch real hunks\n"
            "2) Write a real implementation (not a probe)\n"
            "3) Avoid short probe overwrites of lib.rs/main.rs"
        ),
        "poll_cap": (
            "NEXT:\n"
            "1) Wait for [bg_complete] auto-inject\n"
            "2) edit sources to progress\n"
            "3) Avoid spam poll while still running"
        ),
        "path_env": (
            "NEXT:\n"
            "1) cargo check -p <crate> at project anchor cwd\n"
            "2) Confirm Cargo.toml is in cwd\n"
            "3) Avoid random .rs writes just to pass a gate"
        ),
    }
    return menus.get(kind, "")


def blocked_with_next(message: str, kind: str, **ctx: Any) -> str:
    """Append NEXT menu to a [Blocked] message (one place for pre-gates)."""
    base = (message or "").rstrip()
    menu = next_action_menu(kind, **ctx)
    if not menu:
        return base
    if "NEXT:" in base:
        return base
    return f"{base}\n{menu}"


def deliver_mode_nudge() -> str:
    return (
        "[Deliver lock] Prefer write/check over more scanning.\n"
        "NEXT:\n"
        "1) file_write/edit known compile-error paths\n"
        "2) cargo check -p <crate> (after a real write)\n"
        "3) Avoid dir/where/_diag and whole-file grep"
    )


def pure_read_nudge(*, streak: int) -> str:
    return (
        f"[Read/write imbalance · {streak} pure-read rounds]\n"
        "NEXT:\n"
        "1) file_write/edit product sources\n"
        "2) or cargo check -p <crate>\n"
        "3) Avoid bulk file_read/glob/whole-file grep"
    )


def manage_goal_cadence_nudge() -> str:
    return (
        "[Goal cadence] Todos have not been updated for several rounds. "
        "When progress actually changed, call manage_goal update_todo "
        "(or set_todos). Do **not** open a turn with manage_goal(get) by habit; "
        "prefer real work first, then update the list."
    )


def result_load_nudge(handle_id: str = "") -> str:
    hid = handle_id or "<id>"
    return (
        f"[Result paging] Use result_load(id=\"{hid}\", offset=0, max_chars=20000) "
        "for large output; do not re-file_read the same file or thrash 20-line slices. "
        f"Next page: result_load(id=\"{hid}\", offset=<prior end>, max_chars=20000)."
    )


def l1_truncate_hint(content: str, head: str, tail: str, omitted: int) -> str:
    """Build L1 truncated tool body with result_load guidance when applicable."""
    hid = extract_result_handle(content)
    hint = (
        f"\n…[truncated {omitted} chars by L1 budget — "
        "do NOT re-file_read; page with result_load if handle present]…\n"
    )
    if hid:
        hint = (
            f"\n…[truncated {omitted} chars — call "
            f'result_load(id="{hid}", offset=0, max_chars=20000) for full body; '
            "then raise offset]…\n"
        )
    return head + hint + tail


def resolve_project_anchor(workspace_root: str = "") -> str | None:
    """Prefer tavarn-guardian / guardian under workspace."""
    roots: list[str] = []
    if workspace_root and os.path.isdir(workspace_root):
        roots.append(workspace_root)
    try:
        from backend.tools.permissions import resolve_agent_workspace_root

        wr = resolve_agent_workspace_root()
        if wr and os.path.isdir(wr) and wr not in roots:
            roots.append(wr)
    except Exception:
        pass
    candidates: list[str] = []
    for wr in roots:
        for name in ("tavarn-guardian", "guardian"):
            p = os.path.join(wr, name)
            if os.path.isdir(p) and (
                os.path.isfile(os.path.join(p, "Cargo.toml"))
                or os.path.isdir(os.path.join(p, "crates"))
            ):
                candidates.append(os.path.abspath(p))
        # workspace itself is a rust project
        if os.path.isfile(os.path.join(wr, "Cargo.toml")):
            candidates.append(os.path.abspath(wr))
    return candidates[0] if candidates else (os.path.abspath(roots[0]) if roots else None)


def resume_anchor_block(
    workspace_root: str = "",
    *,
    goal_active: bool = False,
) -> str:
    proj = resolve_project_anchor(workspace_root) or workspace_root or "(workspace)"
    try:
        from backend.core.config import settings as _st

        soft = bool(getattr(_st, "agent_resume_soft_rules", True))
    except Exception:
        soft = True
    if not soft:
        return (
            f"[Project anchor] Code root: `{proj}`\n"
            "Progress: file_write/edit → cargo check"
            + ("; manage_goal when todos change.\n" if goal_active else ".\n")
        )
    if goal_active:
        return (
            f"[Project anchor] `{proj}` (cwd under this tree; "
            "never install/resources fake paths)\n"
            "NEXT:\n"
            "1) Do real work first (read/edit/cargo check)\n"
            "2) manage_goal update_todo only when progress actually changed\n"
            "3) Avoid spam process poll and diagnostic `_` files\n"
        )
    return (
        f"[Project anchor] `{proj}` (cwd under this tree; "
        "never install/resources fake paths)\n"
        "This is a normal chat turn — answer the user directly. "
        "Do **not** open with manage_goal unless they asked about todos/goals.\n"
    )


def cargo_blocked_hint(project: str = "") -> str:
    root = project or "tavarn-guardian"
    return (
        f"Use command: `cd /d {root} && cargo check -p guardian-server` "
        "(or `cargo check -p <crate>`). Job injects MSVC + scoop cargo. "
        "Do not use rustup, python-wrapping cargo, or _diag scripts."
    )


def is_progress_write(tool_name: str, args: dict[str, Any] | None = None) -> bool:
    """True if this is a real source edit (not _snap/_diag dump scripts)."""
    if tool_name not in WRITE_TOOLS:
        return False
    a = args if isinstance(args, dict) else {}
    path = str(
        a.get("path")
        or a.get("filepath")
        or a.get("file")
        or a.get("file_path")
        or a.get("target")
        or ""
    )
    if is_diag_junk_path(path) or _SNAP_WRITE_RE.search(path):
        return False
    # content sniff for dump scripts
    body = str(a.get("content") or a.get("text") or a.get("code") or "")[:200]
    if re.search(r"(?i)Get-Content|Out-File|_snap|dump parts", body):
        return False
    return True


def is_cargo_verify_command(command: str) -> bool:
    return bool(_CARGO_CMD_RE.search(command or ""))


def is_shell_probe_command(command: str) -> bool:
    c = (command or "").strip()
    if not c:
        return False
    if is_cargo_verify_command(c):
        return False
    return bool(_SHELL_PROBE_RE.search(c) or _SHELL_DUMP_RE.search(c))


def is_deliver_allowed_command(command: str) -> bool:
    """In deliver/cargo-fix mode: only cargo verify + light git."""
    c = (command or "").strip()
    if not c:
        return False
    if _SHELL_DUMP_RE.search(c) and not is_cargo_verify_command(c):
        return False
    return bool(_DELIVER_CMD_ALLOW_RE.search(c))


def is_cargo_compile_failure(result: str) -> bool:
    """True only for *source* compile failures (E0xxx / could not compile crate).

    Path/manifest/cwd and bare exit=101 no longer arm must_write_before_cargo
    (see classify_cargo_error). Toggle: agent_cargo_error_class_gate=False
    restores legacy broad matching for emergency rollback.
    """
    try:
        from backend.core.config import settings as _st

        if not bool(getattr(_st, "agent_cargo_error_class_gate", True)):
            t = result or ""
            if is_metadata_stub_error(t):
                return False
            if _CARGO_ERR_RE.search(t):
                return True
            if re.search(r"\[Exit\s+101\b", t) and re.search(
                r"(?i)cargo|Compiling|Checking", t
            ):
                return True
            if re.search(r"status=done\s+exit=101", t, re.I) and re.search(
                r"(?i)\bcargo", t
            ):
                return True
            return False
    except Exception:
        pass
    return classify_cargo_error(result) == "compile_source"


def is_bg_cargo_compile_failure(result: str) -> bool:
    """Process-poll form of cargo fail (auto-bg). Requires done+exit=101."""
    t = result or ""
    if not re.search(r"status=done\s+exit=101", t, re.I):
        return False
    if not re.search(r"(?i)\bcargo(?:\.exe)?\s+(?:check|build|test|clippy)\b", t):
        # still allow if error[E] + cargo in command line snippet
        if not (re.search(r"(?i)\bcargo\b", t) and _CARGO_ERR_RE.search(t)):
            return False
    return is_cargo_compile_failure(t)


def is_bg_cargo_success(result: str) -> bool:
    t = result or ""
    if not re.search(r"status=done\s+exit=0", t, re.I):
        return False
    return bool(
        re.search(r"(?i)\bcargo\b", t)
        and re.search(r"(?i)Finished|exit=0", t)
        and not is_cargo_compile_failure(t)
    )


_BG_ID_RE = re.compile(
    r"(?i)(?:\[bg_complete\s+process_id=|\[bg\s+)(bg_[a-f0-9]+)"
)

# session_id -> cargo_fix arm from proactive bg inject (consumed by tool_round)
_SESSION_CARGO_FIX: dict[str, dict[str, Any]] = {}
_SESSION_BG_NOTIFIED: dict[str, set[str]] = {}


def parse_bg_process_id(text: str) -> str:
    m = _BG_ID_RE.search(text or "")
    return m.group(1) if m else ""


def mark_bg_notified(session_id: str, process_id: str) -> bool:
    """Return True if this is the first notify for pid in this session."""
    sid = str(session_id or "").strip()
    pid = (process_id or "").strip()
    if not sid or not pid:
        return False
    s = _SESSION_BG_NOTIFIED.setdefault(sid, set())
    if pid in s:
        return False
    s.add(pid)
    return True


def arm_session_cargo_fix(
    session_id: str,
    *,
    paths: list[str] | None = None,
    source: str = "bg",
) -> None:
    sid = str(session_id or "").strip()
    if not sid:
        return
    _SESSION_CARGO_FIX[sid] = {
        "paths": list(paths or [])[:5],
        "source": source,
        "must_write": True,
    }


def consume_session_cargo_fix(session_id: str) -> dict[str, Any] | None:
    return _SESSION_CARGO_FIX.pop(str(session_id or "").strip(), None)


def apply_bg_completions_to_messages(
    session_id: str,
    messages: list[dict[str, Any]],
    *,
    max_n: int = 8,
) -> int:
    """Drain pending bg completions into messages; arm cargo_fix if needed.

    Returns number of injections. Safe to call before each LLM round.
    """
    try:
        from backend.services.tools.process_registry import drain_session_completions
    except Exception:
        return 0
    texts = drain_session_completions(str(session_id), max_n=max_n)
    if not texts:
        return 0
    n = 0
    for text in texts:
        pid = parse_bg_process_id(text)
        first = mark_bg_notified(str(session_id), pid) if pid else True
        if not first:
            continue  # already handled (e.g. model polled first)
        messages.append({"role": "system", "content": text})
        n += 1
        if is_bg_cargo_compile_failure(text):
            paths = parse_cargo_error_paths(text)
            arm_session_cargo_fix(str(session_id), paths=paths, source="bg_auto")
            messages.append(
                {
                    "role": "system",
                    "content": cargo_fix_nudge(paths),
                }
            )
        else:
            try:
                _cls = classify_cargo_error(text)
            except Exception:
                _cls = ""
            if _cls == "path_env" and re.search(
                r"(?i)status=done\s+exit=101|\bcargo\b", text
            ):
                # Wrong cwd/manifest: inject anchor hint, do NOT arm must_write
                try:
                    messages.append(
                        {"role": "system", "content": path_env_cargo_nudge()}
                    )
                except Exception:
                    pass
            elif is_bg_cargo_success(text):
                # successful bg cargo — clear pending fix if any
                consume_session_cargo_fix(str(session_id))
    return n


def parse_cargo_error_paths(result: str, *, limit: int = 5) -> list[str]:
    """Extract rustc error file paths from cargo output."""
    found: list[str] = []
    seen: set[str] = set()
    for m in _CARGO_PATH_RE.finditer(result or ""):
        p = m.group(1).replace("\\", "/").strip()
        # strip absolute prefix noise — keep relative-ish
        if "tavarn-guardian/" in p:
            p = p.split("tavarn-guardian/", 1)[-1]
        if p.startswith("crates/") or p.endswith(".rs") or p.endswith(".toml"):
            key = p.lower()
            if key not in seen:
                seen.add(key)
                found.append(p)
        if len(found) >= limit:
            break
    return found


def cargo_fix_nudge(paths: list[str] | None = None) -> str:
    paths = [p for p in (paths or []) if p][:5]
    path_s = ",".join(paths) if paths else ""
    head = (
        "[Compile fail · write first] Prefer a real source edit before "
        "another cargo check."
    )
    if paths:
        head += "\nPaths: " + " | ".join(paths)
    return head + "\n" + next_action_menu(
        "must_write_blocks_cargo", paths=path_s
    )


def no_write_progress_nudge(*, rounds: int) -> str:
    return (
        f"[No write progress · {rounds} rounds]\n"
        "NEXT:\n"
        "1) file_write/edit product sources (not _diag)\n"
        "2) manage_goal: note blockers\n"
        "3) Avoid dump/Get-Content/spam poll"
    )


def extract_tool_args(tc: Any) -> dict[str, Any]:
    args = getattr(tc, "arguments", None)
    if isinstance(args, str):
        try:
            import json

            args = json.loads(args)
        except Exception:
            args = {"_raw": args}
    if isinstance(args, dict):
        return args
    if isinstance(tc, dict):
        a = tc.get("arguments") or (tc.get("function") or {}).get("arguments")
        if isinstance(a, str):
            try:
                import json

                return json.loads(a)
            except Exception:
                return {"_raw": a}
        if isinstance(a, dict):
            return a
    return {}


def command_from_tool(tc: Any) -> str:
    a = extract_tool_args(tc)
    return str(a.get("command") or a.get("cmd") or "")
