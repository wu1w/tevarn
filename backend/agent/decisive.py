"""Heuristics to reduce hesitant single-tool rounds (efficiency, not new tools)."""
from __future__ import annotations

import hashlib
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


# 构建/测试/安装类实质动作不算犹豫窥探（072a783 口径；
# aaffa3c 委托 classify_command 后 pytest 被误判 timid，恢复排除）
_NOT_TIMID_CMDS = frozenset(
    {
        "pytest", "py.test", "npm", "pnpm", "yarn", "pip", "pip3", "uv",
        "cargo", "make", "docker", "docker-compose", "go", "mvn", "gradle",
    }
)


def is_timid_shell_command(command: str) -> bool:
    """True if command is read-only peek (cat/ls/head/git status/...)."""
    c = (command or "").strip()
    if not c:
        return False
    first = c.split()[0].split("/")[-1]
    if first in _NOT_TIMID_CMDS:
        return False
    # 多步串行（&&/;/换行）说明在干活，不算犹豫单读
    if any(x in c for x in ("\n", "&&", ";")):
        return False
    return classify_command(c) == "read"


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
        "[Batch reads] Last turn did only one info-gather (single file_read "
        "or read-only shell). If the task is unfinished: emit multiple "
        "tool_calls this turn — parallel file_read/grep/glob, or if you "
        "have enough context go straight to edit/file_write/command. "
        "Do not peek one file per turn."
    )
    if consecutive_timid >= 2:
        base += (
            " Multiple single-probe rounds already: next turn must batch "
            "reads or start edit/tests; after enough context, edit immediately "
            "and do not re-file_read the same file."
        )
    if consecutive_timid >= 3:
        base += (
            " CRITICAL: 3+ turns with only one tool each. "
            "Batch tool_calls now; if enough info, edit/file_write and stop pure reads."
        )
    return base


# 编制/派活：实测易出现「一轮 7–10 个 crew_steward」空转扇出
_ORCHESTRATION_TOOLS = frozenset(
    {
        "crew_steward",
        "delegate_task",
        "agent_call",
        "manage_sub_agent",
    }
)


def is_orchestration_tool(name: str | None) -> bool:
    return str(name or "").strip() in _ORCHESTRATION_TOOLS


def family_bucket(tool_calls: Iterable[Any] | None) -> str:
    """Collapse thrashy orchestration / result_load-heavy rounds into a stable bucket.

    Exact arg fingerprints miss real-world loops: many crew_steward with *different*
    employee names still make zero product progress. Bucket ≥50% of calls.
    Also buckets cargo verify / shell probe families (OpenHands-style stuck detect).
    """
    names = tool_names_from_calls(tool_calls)
    if not names:
        return ""
    n = len(names)
    orch = sum(1 for x in names if is_orchestration_tool(x))
    rl = sum(1 for x in names if x == "result_load")
    if orch * 2 >= n:  # ≥50%
        return "orch_heavy"
    if rl * 2 >= n:
        return "result_load_heavy"

    # cargo / shell family (tiny command string changes evade exact thrash)
    try:
        from backend.agent.progress_guard import (
            command_from_tool,
            is_cargo_verify_command,
            is_shell_probe_command,
        )

        cargo_n = 0
        probe_n = 0
        process_n = sum(1 for x in names if x == "process")
        for tc in tool_calls or []:
            nm = str(getattr(tc, "name", "") or "")
            if nm == "command":
                cmd = command_from_tool(tc)
                if is_cargo_verify_command(cmd):
                    cargo_n += 1
                elif is_shell_probe_command(cmd):
                    probe_n += 1
            # Do NOT weight bare process as cargo_verify — that forced
            # must_write + deliver while cargo still running (poll thrash).
        if cargo_n * 2 >= n and cargo_n > 0:
            return "cargo_verify"
        if probe_n * 2 >= n and probe_n > 0:
            return "shell_probe"
        if process_n * 2 >= n and process_n > 0:
            return "process_poll"  # poll-only rounds; hard throttle in process_registry
    except Exception:
        pass
    return ""


def thrash_fingerprint(
    tool_calls: Iterable[Any] | None,
    *,
    use_family_bucket: bool = True,
) -> str:
    """Fingerprint for thrash guard; may be fam:* for soft orchestration loops."""
    if use_family_bucket:
        fam = family_bucket(tool_calls)
        if fam:
            return f"fam:{fam}"
    return tool_round_fingerprint(tool_calls)


def orchestration_cap_results(
    tool_calls: list[Any] | None,
    *,
    max_orch: int = 2,
) -> dict[str, str]:
    """Map tool_call_id → synthetic result for orchestration calls beyond max_orch.

    Call list is **not** truncated: every tool_call_id still needs a tool message
    for the next LLM round. Excess crew/delegate calls skip real execute.
    """
    max_orch = max(0, int(max_orch))
    out: dict[str, str] = {}
    if max_orch <= 0:
        return out
    orch_seen = 0
    for tc in tool_calls or []:
        name = getattr(tc, "name", None)
        if name is None and isinstance(tc, dict):
            name = (tc.get("function") or {}).get("name") or tc.get("name")
        cid = str(getattr(tc, "id", None) or (tc.get("id") if isinstance(tc, dict) else "") or "")
        if not cid or not is_orchestration_tool(str(name or "")):
            continue
        if orch_seen < max_orch:
            orch_seen += 1
            continue
        out[cid] = (
            f"[Orchestration cap] {name} hit the per-round max ({max_orch}); "
            "extra crew/delegate calls were skipped. Digest existing jobs / "
            "result_load first, then do real work (files/commands/goal). "
            "Do not spam empty dispatch. Reply to the user in their language."
        )
    return out


def tool_round_fingerprint(tool_calls: Iterable[Any] | None) -> str:
    """Stable fingerprint for a tool round (detect thrash / no-progress loops)."""
    parts: list[str] = []
    for tc in tool_calls or []:
        name = getattr(tc, "name", None)
        if name is None and isinstance(tc, dict):
            name = (tc.get("function") or {}).get("name") or tc.get("name")
        args = _tool_args(tc)
        # keep path-ish keys only (ignore volatile ids)
        slim: dict[str, Any] = {}
        for k in (
            "path",
            "file",
            "filepath",
            "file_path",
            "pattern",
            "query",
            "command",
            "cmd",
            "action",
            "name",
            "glob",
            "url",
            "result_id",
            "id",
            "key",
            # pagination — without these, multi-offset file_read looks like thrash
            "offset",
            "start",
            "start_line",
            "line",
            "lines",
            "limit",
            "max_lines",
            "max_chars",
            "end",
            "end_line",
            # python / write payloads — without these, successive python scaffolds
            # all fingerprint as python|{} → false thrash force_final mid-task
            "code",
            "script",
            "source",
            "content",
            "text",
            "body",
            "data",
        ):
            if k in args and args[k] is not None:
                v = str(args[k])
                # long code: keep head+len+hash so different scripts differ
                if k in ("code", "script", "source", "content", "text", "body", "data") and len(v) > 240:
                    h = hashlib.sha256(v.encode("utf-8", errors="replace")).hexdigest()[:12]
                    slim[k] = f"{v[:120]}…#{len(v)}:{h}"
                else:
                    slim[k] = v[:200]
        if not slim and args:
            # fallback: sorted key names + short values
            for k in sorted(str(x) for x in args.keys())[:8]:
                slim[k] = str(args.get(k))[:80]
        raw = f"{name}|{json.dumps(slim, ensure_ascii=False, sort_keys=True)}"
        parts.append(raw)
    parts.sort()
    blob = "\n".join(parts)
    return hashlib.sha256(blob.encode("utf-8", errors="replace")).hexdigest()[:20]


def is_tool_thrash(
    prev_fp: str | None,
    curr_fp: str,
    *,
    thrash_streak: int,
    force_after: int = 2,
) -> bool:
    """True when consecutive tool rounds look identical (no progress)."""
    if not curr_fp or not prev_fp:
        return False
    if prev_fp != curr_fp:
        return False
    return int(thrash_streak) + 1 >= max(1, int(force_after))


def thrash_force_final_text(*, family: str = "") -> str:
    """Short system injects. Avoid demanding multi-section inventory reports."""
    if family == "orch_heavy" or family.startswith("fam:orch"):
        return (
            "[Orch thrash] Many crew_steward/delegate rounds with low gain. "
            "Prefer a short status in the user's language; avoid long inventories."
        )
    if family == "result_load_heavy" or family.startswith("fam:result_load"):
        return (
            "[Result-load thrash] Many result_load rounds. "
            "Conclude from what you already have; stop re-paging in a loop."
        )
    if family == "cargo_verify" or family.startswith("fam:cargo"):
        return (
            "[Cargo thrash] Many cargo check/build rounds without real writes. "
            "Prefer file_write/edit on error paths before another pure check. "
            "If check already passed, manage_goal + a short summary."
        )
    if family == "shell_probe" or family.startswith("fam:shell"):
        return (
            "[Shell probe thrash] Many where/dir/Get-Content scans. "
            "Prefer file_write on product sources or cargo check; avoid _snap dumps."
        )
    return (
        "[Tool thrash] Same tool/args repeated with zero information gain. "
        "Give a short handoff in the user's language; do not restate long "
        "status inventories or force-final essays."
    )


def batch_write_nudge_text(*, consecutive_timid: int = 1) -> str:
    """Package/multi-file: nudge parallel file_write after a single write."""
    base = (
        "[Batch writes] Last turn only file_write/edit one file. "
        "If still scaffolding a package: emit multiple file_write in one turn "
        "(__init__.py, modules, tests, pyproject, …), then one pytest. "
        "Do not write one file per turn."
    )
    if consecutive_timid >= 2:
        base += (
            " Repeated single-file writes: next turn batch file_write or run tests."
        )
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
