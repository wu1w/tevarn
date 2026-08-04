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
            f"[Orchestration cap] 本轮 {name} 已达上限 {max_orch}，"
            "已跳过多余编制调用。请先消化已派工单/result_load 结果，"
            "用中文推进实质工作（读写文件/命令/目标），勿再批量空派。"
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
        ):
            if k in args and args[k] is not None:
                slim[k] = str(args[k])[:200]
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
    if family == "orch_heavy" or family.startswith("fam:orch"):
        return (
            "【强制收束·编制空转】你已连续多轮以 crew_steward/delegate 为主，"
            "信息增益很低。下一轮**禁止**再调工具：直接汇总已有工单/结果给主人"
            "（做了什么 / 卡点 / 下一步）。不要再批量派工或重复 result_load。"
        )
    if family == "result_load_heavy" or family.startswith("fam:result_load"):
        return (
            "【强制收束·结果回读空转】你已连续多轮反复 result_load。"
            "下一轮**禁止**再调工具：用已读内容直接写结论；缺什么明确列出，不要再转圈回读。"
        )
    return (
        "【强制收束】你已连续多轮调用**相同工具/相同参数**，信息增益为零。"
        "下一轮**禁止**再调工具：直接用已有结果给主人完整中文结论"
        "（做了什么 / 结果 / 风险 / 下一步）。不要再 grep/file_read 同一路径。"
    )


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
