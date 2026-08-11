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
    light_loop: bool = False,
    ops_loop: bool = False,
) -> str:
    """research | implement | steward | chat. Prefer Rust loop_guard_resolve_role."""
    pl = payload if isinstance(payload, dict) else {}
    try:
        import os

        be = (os.environ.get("TAKTON_KERNEL_BACKEND") or "").strip().lower()
        if be not in {"python", "py", "off", "0", "none"}:
            from backend.kernel import get_kernel

            k = get_kernel()
            params = {
                "workforce": bool(workforce),
                "identity_name": identity_name or "",
                "identity_role": identity_role or "",
                "instruction": instruction or "",
                "light_loop": bool(light_loop),
                "ops_loop": bool(ops_loop),
                "payload": pl,
                "role_kind": pl.get("role_kind") or pl.get("role"),
            }
            r = None
            if hasattr(k, "loop_guard_resolve_role"):
                r = k.loop_guard_resolve_role(**params)
            elif hasattr(k, "_call"):
                r = k._call("loop_guard_resolve_role", params)
            if isinstance(r, dict) and r.get("role_kind"):
                rk = str(r.get("role_kind") or "").strip().lower()
                if rk in ("research", "implement", "steward", "chat"):
                    return rk
    except Exception:
        pass
    # Python fallback (no force chat→steward)
    if light_loop or ops_loop:
        return "chat"
    explicit = str(pl.get("role_kind") or pl.get("role") or "").strip().lower()
    if explicit in ("research", "implement", "steward", "chat", "explore"):
        return "research" if explicit == "explore" else explicit
    blob = f"{identity_name or ''} {identity_role or ''}"
    if re.search(r"(研究|research|explore|分析师)", blob, re.I):
        return "research"
    if re.search(r"(CEO|管家|steward|大管家)", blob, re.I) and not workforce:
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
    light_loop: bool = False,
    ops_loop: bool = False,
    max_iters: int | None = None,
    harness_mode: str | None = None,
) -> dict[str, Any]:
    """Build loop_guard config. Prefer full Rust plan; no Python force-steward."""
    pl = payload if isinstance(payload, dict) else {}
    try:
        import os

        be = (os.environ.get("TAKTON_KERNEL_BACKEND") or "").strip().lower()
        if be not in {"python", "py", "off", "0", "none"}:
            from backend.kernel import get_kernel

            k = get_kernel()
            params: dict[str, Any] = {
                "workforce": bool(workforce),
                "identity_name": identity_name or "",
                "identity_role": identity_role or "",
                "instruction": instruction or "",
                "light_loop": bool(light_loop),
                "ops_loop": bool(ops_loop),
                "payload": pl,
                "role_kind": pl.get("role_kind") or pl.get("role"),
                "thoroughness": pl.get("thoroughness"),
                "harness_mode": harness_mode
                or pl.get("harness_mode")
                or "",
            }
            if max_iters is not None:
                params["max_iters"] = int(max_iters)
            r = None
            if hasattr(k, "loop_guard_resolve_role"):
                r = k.loop_guard_resolve_role(**params)
            elif hasattr(k, "_call"):
                r = k._call("loop_guard_resolve_role", params)
            if isinstance(r, dict) and r.get("role_kind"):
                cfg = dict(r)
                # settings overrides still apply
                try:
                    from backend.core.config import settings

                    role = str(cfg.get("role_kind") or "")
                    if role == "implement":
                        ov = int(getattr(settings, "agent_worker_max_tool_rounds", 0) or 0)
                        if ov > 0:
                            cfg["max_tool_rounds"] = ov
                    if role == "research":
                        ov_r = int(
                            getattr(settings, "agent_research_max_tool_rounds", 0) or 0
                        )
                        if ov_r > 0:
                            cfg["max_tool_rounds"] = ov_r
                    if role == "chat" and max_iters:
                        cfg["max_tool_rounds"] = int(max_iters)
                    elif role == "chat":
                        cfg["max_tool_rounds"] = int(
                            getattr(settings, "agent_light_l0_max_iters", 8) or 8
                        )
                    if not cfg.get("ban_worker_orch"):
                        cap = int(
                            getattr(settings, "agent_crew_steward_max_per_run", 0) or 0
                        )
                        if cap > 0:
                            cfg["max_crew_total"] = cap
                        orch = getattr(settings, "agent_max_orch_tools_per_round", None)
                        if orch is not None:
                            cfg["max_orch_per_round"] = max(0, int(orch))
                    ratio = float(
                        getattr(settings, "agent_budget_force_ratio", 0.85) or 0.85
                    )
                    if 0 < ratio <= 1:
                        cfg["budget_force_ratio"] = ratio
                except Exception:
                    pass
                if cfg.get("thoroughness") is None:
                    cfg.pop("thoroughness", None)
                return cfg
    except Exception as e:
        logger.debug("loop_guard_resolve_role fallback: %s", e)

    role = classify_role_kind(
        workforce=workforce,
        identity_name=identity_name,
        identity_role=identity_role,
        instruction=instruction,
        payload=payload,
        light_loop=light_loop,
        ops_loop=ops_loop,
    )
    thoroughness = resolve_thoroughness(payload, instruction)
    cfg: dict[str, Any] = {
        "workforce": bool(workforce),
        "role_kind": role,
        "thoroughness": thoroughness if role == "research" else None,
        "budget_force_ratio": 0.85,
    }
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
        cfg["max_tool_rounds"] = int((pl or {}).get("max_tool_rounds") or 20)
        cfg["max_file_reads"] = 50
        cfg["max_crew_total"] = 0
        cfg["max_orch_per_round"] = 0
        cfg["ban_worker_orch"] = True
    elif role == "steward":
        cfg["max_tool_rounds"] = 40
        cfg["max_crew_total"] = 3
        cfg["max_orch_per_round"] = 1
        cfg["ban_worker_orch"] = False
    else:
        cfg["max_tool_rounds"] = int(max_iters or 8)
        cfg["max_file_reads"] = 16
        cfg["max_crew_total"] = 0
        cfg["max_orch_per_round"] = 0
        cfg["ban_worker_orch"] = True
    try:
        from backend.core.config import settings

        if role in ("research", "implement"):
            ov = int(getattr(settings, "agent_worker_max_tool_rounds", 0) or 0)
            if ov > 0 and role == "implement":
                cfg["max_tool_rounds"] = ov
            ov_r = int(getattr(settings, "agent_research_max_tool_rounds", 0) or 0)
            if ov_r > 0 and role == "research":
                cfg["max_tool_rounds"] = ov_r
        if role == "chat":
            cfg["max_tool_rounds"] = int(
                max_iters
                or getattr(settings, "agent_light_l0_max_iters", 8)
                or 8
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
    st = _LOCAL.setdefault(pid, {"config": {}, "tool_rounds": 0, "crew_total": 0, "trunc": {}})
    cfg = st.get("config") or {}
    if cfg.get("ban_worker_orch") and name in _ORCH:
        return {
            "status": "block",
            "code": "worker_orch_banned",
            "tool": name,
            "message": (
                f"[LoopGuard] 子工单禁止再调用 {name}。请直接完成任务并给出结论，勿再派工。"
            ),
        }
    if name in ("file_read", "read"):
        path = str(a.get("path") or a.get("file") or a.get("file_path") or "")
        has_offset = bool(a.get("offset") or a.get("start_line") or a.get("limit"))
        if path and st.get("trunc", {}).get(path) and not has_offset:
            return {
                "status": "block",
                "code": "truncated_reread_blocked",
                "tool": name,
                "message": (
                    f"[LoopGuard] 文件已截断读过：{path}\n"
                    "禁止整文件重读。请用 offset/limit 或 grep。"
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
        st = _LOCAL.setdefault(pid, {"config": {}, "tool_rounds": 0, "crew_total": 0, "trunc": {}})
        if name in _ORCH:
            st["crew_total"] = int(st.get("crew_total") or 0) + 1
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
    if code in ("max_tool_rounds",):
        return (
            "【强制收束·轮次上限】本工单工具轮次已达硬顶（对齐 Claude Code max_turns）。"
            "下一轮禁止再调工具：用中文列出已完成/未完成、证据路径与卡点。"
            + (f" ({reason})" if reason else "")
        )
    if code in ("budget_ratio",):
        return (
            "【强制收束·预算 85%】Token 预算将尽。禁止再调工具；"
            "立即给出当前结论与未完成项，勿写空报告框架。"
            + (f" ({reason})" if reason else "")
        )
    if code in ("orch_window_thrash", "crew_total_cap"):
        return (
            "【强制收束·编制空转】编制/派工已达上限或滑动窗口 thrash。"
            "禁止再 crew_steward/delegate：汇总已有工单结果给主人。"
            + (f" ({reason})" if reason else "")
        )
    return (
        "【强制收束】LoopGuard 触发。"
        "下一轮禁止工具，直接中文终答。"
        + (f" code={code} {reason}" if code or reason else "")
    )


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
