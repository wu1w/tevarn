"""Python bridge to Rust kernel loop_guard (PR1–PR4 thrash/fan-out).

Falls back to local pure-Python decisions when host/kernel is unavailable
so unit tests and degraded modes still enforce worker bans.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_ORCH = frozenset(
    {"crew_steward", "delegate_task", "agent_call", "manage_sub_agent"}
)
_RESEARCH_HINT = re.compile(
    r"(调研|研究|research|explore|只读|read.?only|GitHub|MCP|评估|分析|"
    r"怎么实现|最小改动|成熟做法|官方)",
    re.I,
)


def classify_role_kind(
    *,
    workforce: bool,
    identity_name: str | None = None,
    identity_role: str | None = None,
    instruction: str | None = None,
    payload: dict[str, Any] | None = None,
) -> str:
    """research | implement | steward | chat."""
    pl = payload if isinstance(payload, dict) else {}
    explicit = str(pl.get("role_kind") or pl.get("role") or "").strip().lower()
    if explicit in ("research", "implement", "steward", "chat", "explore"):
        return "research" if explicit == "explore" else explicit
    blob = f"{identity_name or ''} {identity_role or ''}"
    if re.search(r"(研究|research|explore|分析师)", blob, re.I):
        return "research"
    if re.search(r"(CEO|管家|steward|编制)", blob, re.I) and not workforce:
        return "steward"
    instr = instruction or ""
    if workforce and _RESEARCH_HINT.search(instr):
        return "research"
    if workforce:
        return "implement"
    return "chat"


def resolve_thoroughness(payload: dict[str, Any] | None, instruction: str | None) -> str:
    pl = payload if isinstance(payload, dict) else {}
    t = str(pl.get("thoroughness") or "").strip().lower()
    if t in ("quick", "medium", "very_thorough", "thorough", "deep"):
        if t in ("thorough", "deep"):
            return "very_thorough"
        return t
    instr = instruction or ""
    if re.search(r"(快速|粗看|quick|扫一眼)", instr, re.I):
        return "quick"
    if re.search(r"(深入|穷尽|全面|very.?thorough|deep)", instr, re.I):
        return "very_thorough"
    return "medium"


def build_loop_guard_config(
    *,
    workforce: bool,
    identity_name: str | None = None,
    identity_role: str | None = None,
    instruction: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    role = classify_role_kind(
        workforce=workforce,
        identity_name=identity_name,
        identity_role=identity_role,
        instruction=instruction,
        payload=payload,
    )
    thoroughness = resolve_thoroughness(payload, instruction)
    cfg: dict[str, Any] = {
        "workforce": bool(workforce),
        "role_kind": role,
        "thoroughness": thoroughness if role == "research" else None,
        "budget_force_ratio": 0.85,
    }
    pl = payload if isinstance(payload, dict) else {}
    if pl.get("max_tool_rounds") is not None:
        try:
            cfg["max_tool_rounds"] = int(pl["max_tool_rounds"])
        except Exception:
            pass
    if pl.get("token_budget") is not None:
        # not used by configure directly; budget comes from process
        pass
    # Defaults match Rust LoopGuardConfig::for_role
    if role == "research":
        th = thoroughness
        cfg["max_tool_rounds"] = {"quick": 6, "medium": 12, "very_thorough": 16}.get(
            th, 12
        )
        cfg["max_file_reads"] = {"quick": 8, "medium": 20, "very_thorough": 40}.get(
            th, 20
        )
        cfg["max_crew_total"] = 0
        cfg["max_orch_per_round"] = 0
        cfg["ban_worker_orch"] = True
    elif role == "implement":
        # Scaffolding multi-crate projects needs more than 20 write rounds
        cfg["max_tool_rounds"] = int(pl.get("max_tool_rounds") or 60)
        cfg["max_file_reads"] = 80
        cfg["max_crew_total"] = 0
        cfg["max_orch_per_round"] = 0
        cfg["ban_worker_orch"] = True
    elif role == "steward":
        # Goal / coding steward: generous dispatch headroom for multi-hire
        cfg["max_tool_rounds"] = int(pl.get("max_tool_rounds") or 100)
        cfg["max_file_reads"] = int(pl.get("max_file_reads") or 80)
        cfg["max_crew_total"] = 999
        cfg["max_orch_per_round"] = 24
        cfg["ban_worker_orch"] = False
    else:
        cfg["max_tool_rounds"] = int(pl.get("max_tool_rounds") or 80)
        cfg["max_file_reads"] = int(pl.get("max_file_reads") or 80)
        cfg["max_crew_total"] = 999
        cfg["max_orch_per_round"] = 24
        cfg["ban_worker_orch"] = False
    # Settings overrides
    try:
        from backend.core.config import settings

        if role in ("research", "implement"):
            ov = int(getattr(settings, "agent_worker_max_tool_rounds", 0) or 0)
            if ov > 0 and role == "implement":
                cfg["max_tool_rounds"] = ov
            ov_r = int(getattr(settings, "agent_research_max_tool_rounds", 0) or 0)
            if ov_r > 0 and role == "research":
                cfg["max_tool_rounds"] = ov_r
        # Chat/steward coding: align with goal iteration budget when higher
        if role in ("chat", "steward"):
            try:
                goal_iters = int(
                    getattr(settings, "agent_goal_max_iterations", 100) or 100
                )
            except Exception:
                goal_iters = 100
            try:
                chat_iters = int(getattr(settings, "agent_max_iterations", 40) or 40)
            except Exception:
                chat_iters = 40
            cfg["max_tool_rounds"] = max(
                int(cfg.get("max_tool_rounds") or 40),
                goal_iters,
                chat_iters,
            )
        cap = int(getattr(settings, "agent_crew_steward_max_per_run", 0) or 0)
        if cap > 0 and not cfg.get("ban_worker_orch"):
            cfg["max_crew_total"] = cap
        orch = getattr(settings, "agent_max_orch_tools_per_round", None)
        if orch is not None and not cfg.get("ban_worker_orch"):
            cfg["max_orch_per_round"] = max(0, int(orch))
        ratio = float(getattr(settings, "agent_budget_force_ratio", 0.85) or 0.85)
        if 0 < ratio <= 1:
            cfg["budget_force_ratio"] = ratio
        # Soft-open: never trip steward on tiny crew/orch caps (user: open walls)
        try:
            from backend.agent.progress_guard import soft_open_mode

            if soft_open_mode() and not cfg.get("ban_worker_orch"):
                cfg["max_crew_total"] = max(int(cfg.get("max_crew_total") or 0), 999)
                cfg["max_orch_per_round"] = max(
                    int(cfg.get("max_orch_per_round") or 0), 24
                )
        except Exception:
            pass
    except Exception:
        pass
    if cfg.get("thoroughness") is None:
        cfg.pop("thoroughness", None)
    return cfg


def _kernel_call(method: str, params: dict[str, Any]) -> dict[str, Any] | None:
    try:
        from backend.kernel import get_kernel

        k = get_kernel()
        if method == "loop_guard_configure" and hasattr(k, "loop_guard_configure"):
            pid = str(params.get("process_id") or "")
            cfg = params.get("config")
            if not isinstance(cfg, dict):
                cfg = {kk: vv for kk, vv in params.items() if kk != "process_id"}
            return k.loop_guard_configure(pid, cfg) or {}
        if method == "loop_guard_begin_round" and hasattr(k, "loop_guard_begin_round"):
            return (
                k.loop_guard_begin_round(
                    str(params.get("process_id") or ""),
                    list(params.get("tool_names") or []),
                )
                or {}
            )
        if method == "loop_guard_pre_tool" and hasattr(k, "loop_guard_pre_tool"):
            return (
                k.loop_guard_pre_tool(
                    str(params.get("process_id") or ""),
                    str(params.get("tool") or ""),
                    params.get("args") if isinstance(params.get("args"), dict) else {},
                )
                or {}
            )
        if method == "loop_guard_post_tool" and hasattr(k, "loop_guard_post_tool"):
            return (
                k.loop_guard_post_tool(
                    str(params.get("process_id") or ""),
                    str(params.get("tool") or ""),
                    params.get("args") if isinstance(params.get("args"), dict) else {},
                    result=params.get("result"),
                    truncated=params.get("truncated"),
                )
                or {}
            )
        if method == "loop_guard_budget_check" and hasattr(k, "loop_guard_budget_check"):
            return k.loop_guard_budget_check(str(params.get("process_id") or "")) or {}
        if hasattr(k, "_call"):
            return k._call(method, params) or {}
    except Exception as e:
        logger.debug("loop_guard %s skip: %s", method, e)
    return None


def configure_for_process(process_id: str, config: dict[str, Any]) -> dict[str, Any]:
    pid = (process_id or "").strip()
    if not pid:
        return {"status": "skip", "reason": "no_process"}
    r = _kernel_call(
        "loop_guard_configure",
        {"process_id": pid, "config": config},
    )
    if isinstance(r, dict) and r:
        # also keep local mirror for unit tests without host
        _LOCAL.setdefault(
            pid, {"config": config, "tool_rounds": 0, "crew_total": 0, "trunc": {}}
        )
        _LOCAL[pid]["config"] = config
        return r
    # local fallback store
    _LOCAL.setdefault(pid, {"config": config, "tool_rounds": 0, "crew_total": 0, "trunc": {}})
    _LOCAL[pid]["config"] = config
    return {"status": "local", "config": config}


_LOCAL: dict[str, dict[str, Any]] = {}


def begin_round(process_id: str, tool_names: list[str]) -> dict[str, Any]:
    pid = (process_id or "").strip()
    if not pid:
        return {"status": "allow"}
    r = _kernel_call(
        "loop_guard_begin_round",
        {"process_id": pid, "tool_names": list(tool_names or [])},
    )
    if isinstance(r, dict) and r.get("status"):
        return r
    # local fallback max rounds
    st = _LOCAL.setdefault(pid, {"config": {}, "tool_rounds": 0, "crew_total": 0, "trunc": {}})
    st["tool_rounds"] = int(st.get("tool_rounds") or 0) + 1
    mx = int((st.get("config") or {}).get("max_tool_rounds") or 40)
    if st["tool_rounds"] > mx:
        return {
            "status": "force_final",
            "code": "max_tool_rounds",
            "reason": f"tool rounds {st['tool_rounds']} > {mx}",
            "action": "force_final_no_tools",
        }
    return {"status": "allow", "process_id": pid}


def pre_tool(process_id: str, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    pid = (process_id or "").strip()
    name = (tool or "").strip()
    a = args if isinstance(args, dict) else {}
    if not pid:
        # still ban orch if local workforce config unknown → allow
        return {"status": "allow"}
    r = _kernel_call(
        "loop_guard_pre_tool",
        {"process_id": pid, "tool": name, "args": a},
    )
    if isinstance(r, dict) and r.get("status"):
        return r
    # local fallback
    st = _LOCAL.setdefault(
        pid,
        {
            "config": {},
            "tool_rounds": 0,
            "crew_total": 0,
            "trunc": {},
            "file_reads": 0,
            "path_reads": {},
        },
    )
    cfg = st.get("config") or {}
    if cfg.get("ban_worker_orch") and name in _ORCH:
        return {
            "status": "block",
            "code": "worker_orch_banned",
            "tool": name,
            "message": (
                f"[LoopGuard] Worker jobs must not call {name}. "
                "Finish the task and conclude; do not re-dispatch."
            ),
        }
    if name in ("file_read", "read"):
        path = str(a.get("path") or a.get("file") or a.get("file_path") or "")
        # diag junk paths
        try:
            from backend.agent.progress_guard import is_diag_junk_path
            from backend.core.config import settings as _st

            if bool(getattr(_st, "agent_ignore_diag_junk_paths", True)) and path and is_diag_junk_path(path):
                return {
                    "status": "block",
                    "code": "diag_junk_blocked",
                    "tool": name,
                    "message": (
                        f"[LoopGuard] Ignore diagnostic junk path: {path}\n"
                        "Do not read _cargo_*/_diag_*/_hello*; edit product sources or cargo check."
                    ),
                }
        except Exception:
            pass
        mx_fr = int(cfg.get("max_file_reads") or 80)
        fr = int(st.get("file_reads") or 0)
        if mx_fr > 0 and fr >= mx_fr:
            return {
                "status": "block",
                "code": "max_file_reads",
                "tool": name,
                "message": (
                    f"[LoopGuard] file_read cap reached for this run ({mx_fr}). "
                    "Prefer file_write/edit + cargo check + manage_goal; "
                    "page large output with result_load."
                ),
            }
        # Same-path re-read cap (Claude Code community pattern / OpenHands stuck)
        if path:
            try:
                from backend.core.config import settings as _st2

                _cap = max(1, int(getattr(_st2, "agent_same_path_reread_max", 3) or 3))
            except Exception:
                _cap = 3
            _key = path.replace("\\", "/").lower()
            _pr = st.setdefault("path_reads", {})
            _cnt = int(_pr.get(_key) or 0)
            has_offset = bool(a.get("offset") or a.get("start_line") or a.get("limit"))
            # allow 1 extra if offset pagination
            _eff = _cap + (1 if has_offset else 0)
            if _cnt >= _eff:
                return {
                    "status": "block",
                    "code": "same_path_reread",
                    "tool": name,
                    "message": (
                        f"[LoopGuard] Same path read {_cnt} times: {path}\n"
                        "Prefer file_write/edit from what you have, or narrow grep."
                    ),
                }
        has_offset = bool(a.get("offset") or a.get("start_line") or a.get("limit"))
        if path and st.get("trunc", {}).get(path) and not has_offset:
            return {
                "status": "block",
                "code": "truncated_reread_blocked",
                "tool": name,
                "message": (
                    f"[LoopGuard] File already read truncated: {path}\n"
                    "Do not re-read whole file; use offset/limit, grep, or result_load."
                ),
            }
    return {"status": "allow", "process_id": pid}


def post_tool(
    process_id: str,
    tool: str,
    args: dict[str, Any] | None = None,
    result: str | None = None,
    truncated: bool | None = None,
) -> dict[str, Any]:
    pid = (process_id or "").strip()
    name = (tool or "").strip()
    a = args if isinstance(args, dict) else {}
    text = result or ""
    if truncated is None:
        truncated = (
            "omitted for LLM" in text
            or "chars omitted" in text
            or "[truncated]" in text
            or "persisted-output" in text
            or "more lines]" in text
        )
    if pid:
        r = _kernel_call(
            "loop_guard_post_tool",
            {
                "process_id": pid,
                "tool": name,
                "args": a,
                "result": text[:4000],
                "truncated": bool(truncated),
            },
        )
        if isinstance(r, dict):
            return r
        st = _LOCAL.setdefault(
            pid,
            {
                "config": {},
                "tool_rounds": 0,
                "crew_total": 0,
                "trunc": {},
                "file_reads": 0,
                "path_reads": {},
            },
        )
        if name in _ORCH:
            st["crew_total"] = int(st.get("crew_total") or 0) + 1
        if name in ("file_read", "read"):
            st["file_reads"] = int(st.get("file_reads") or 0) + 1
            path = str(a.get("path") or a.get("file") or a.get("file_path") or "")
            if path:
                _key = path.replace("\\", "/").lower()
                _pr = st.setdefault("path_reads", {})
                _pr[_key] = int(_pr.get(_key) or 0) + 1
        if truncated and name in ("file_read", "read"):
            path = str(a.get("path") or a.get("file") or a.get("file_path") or "")
            if path:
                st.setdefault("trunc", {})[path] = True
    return {"status": "ok"}


def budget_check(process_id: str) -> dict[str, Any]:
    pid = (process_id or "").strip()
    if not pid:
        return {"status": "allow"}
    r = _kernel_call("loop_guard_budget_check", {"process_id": pid})
    if isinstance(r, dict) and r.get("status"):
        return r
    return {"status": "allow", "process_id": pid}


def force_final_message(code: str, reason: str = "") -> str:
    """Short controller note (LoopDecision) — avoid long system essays."""
    try:
        from backend.agent.loop_decision import from_guard_code

        note = from_guard_code(code, reason).as_controller_note()
        if note:
            return f"[Controller] {note}"
    except Exception:
        pass
    return (
        "[Controller] Stop tools and answer the user now. "
        + (f"code={code} {reason}" if code or reason else "")
    ).strip()



def reset_local_for_tests() -> None:
    _LOCAL.clear()


__all__ = [
    "classify_role_kind",
    "resolve_thoroughness",
    "build_loop_guard_config",
    "configure_for_process",
    "begin_round",
    "pre_tool",
    "post_tool",
    "budget_check",
    "force_final_message",
    "reset_local_for_tests",
]
