"""Default-path runtime health + recovery actions (analysis P0 #1/#3).

Unified snapshot for chat recovery cards and Kernel dashboard.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def collect_runtime_health() -> dict[str, Any]:
    """Probe host, sandbox, budget policy, court mode — no exceptions to caller."""
    from backend.agent.exit_reasons import describe_exit_reason

    issues: list[dict[str, Any]] = []
    actions: list[dict[str, str]] = []

    # ── Host / ABI ──
    host_up = False
    abi_ok = False
    host_status: dict[str, Any] = {}
    try:
        from backend.kernel import get_kernel, get_kernel_backend

        backend = get_kernel_backend()
        k = get_kernel()
        if hasattr(k, "host_runtime_status"):
            host_status = k.host_runtime_status() or {}
            host_up = bool(host_status.get("up"))
            abi_ok = bool((host_status.get("abi") or {}).get("ok"))
        elif hasattr(k, "_call"):
            host_up = True
            try:
                methods = (k._call("list_methods") or {}).get("methods") or []
                from backend.kernel_rust.abi_gate import check_required_abi

                abi = check_required_abi(methods)
                abi_ok = bool(abi.get("ok"))
                host_status = {
                    "up": True,
                    "abi": abi,
                    "methods_count": len(methods),
                    "host_epoch": int(getattr(k, "_host_epoch", 0) or 0),
                }
            except Exception as e:
                host_up = False
                host_status = {"up": False, "error": str(e)}
        else:
            backend = "python"
            host_status = {"up": False, "backend": "python"}
        # 确保 epoch 始终可见（host wipe 后前端清 process UI）
        if "host_epoch" not in host_status:
            try:
                host_status["host_epoch"] = int(getattr(k, "_host_epoch", 0) or 0)
            except Exception:
                host_status["host_epoch"] = 0
    except Exception as e:
        backend = "unknown"
        host_status = {"up": False, "error": str(e), "host_epoch": 0}
        err = str(e).lower()
        if "abi" in err or "missing" in err:
            issues.append(describe_exit_reason("host_abi_mismatch"))
            actions.append(
                {
                    "id": "rebuild_host",
                    "label": "重建 Host",
                    "hint": "cargo build -p takton-kernel-host --release",
                }
            )
        else:
            issues.append(describe_exit_reason("host_down"))
            actions.append(
                {
                    "id": "restart_host",
                    "label": "重启 Host",
                    "path": "/api/kernel/host/restart",
                }
            )

    if not host_up and not any(i.get("code") == "host_down" for i in issues):
        issues.append(describe_exit_reason("host_down"))
        actions.append(
            {
                "id": "restart_host",
                "label": "重启 Host",
                "path": "/api/kernel/host/restart",
            }
        )
    # False positive guard: UI side-channel returns methods=[] on host timeout.
    # That is "host stuck/unresponsive", NOT a real ABI mismatch (missing methods).
    methods_count = int(
        host_status.get("methods_count")
        or (host_status.get("abi") or {}).get("have")
        or 0
    )
    abi_info = host_status.get("abi") or {}
    host_error = str(host_status.get("error") or abi_info.get("error") or "")
    looks_like_timeout = (
        methods_count == 0
        or "timeout" in host_error.lower()
        or "timed out" in host_error.lower()
        or bool(abi_info.get("degraded"))
    )
    if (
        host_up
        and not abi_ok
        and looks_like_timeout
        and not any(i.get("code") == "host_down" for i in issues)
    ):
        issues.append(describe_exit_reason("host_down"))
        actions.append(
            {
                "id": "restart_host",
                "label": "重启 Host（疑似卡死，非 ABI 缺方法）",
                "path": "/api/kernel/host/restart",
            }
        )
    elif (
        host_up
        and not abi_ok
        and not looks_like_timeout
        and not any(i.get("code") == "host_abi_mismatch" for i in issues)
    ):
        issues.append(describe_exit_reason("host_abi_mismatch"))
        actions.append(
            {
                "id": "rebuild_host",
                "label": "重建并 stage Host",
                "hint": ".\\scripts\\build-kernel-host.ps1 -Release",
            }
        )

    # ── Sandbox capability (honest: full vs restricted vs none) ──
    sandbox: dict[str, Any] = {"ok": True, "full_isolation": False, "level": "unknown"}
    try:
        from backend.computer.detect import detect_sandbox_capability

        cap = detect_sandbox_capability()
        # dataclass SandboxCapability: mode/level/available/label/note
        available = bool(
            getattr(cap, "available", None)
            or getattr(cap, "ok", None)
            or (isinstance(cap, dict) and (cap.get("available") or cap.get("ok")))
        )
        mode = getattr(cap, "mode", None) or (
            cap.get("mode") if isinstance(cap, dict) else None
        )
        level = getattr(cap, "level", None) or (
            cap.get("level") if isinstance(cap, dict) else None
        ) or ("full" if available else "none")
        label = getattr(cap, "label", None) or (
            cap.get("label") if isinstance(cap, dict) else None
        )
        note = getattr(cap, "note", None) or (
            cap.get("note") if isinstance(cap, dict) else ""
        ) or ""
        sandbox = {
            "ok": available,
            "backend": mode,
            "mode": mode,
            "level": level,
            "label": label,
            "note": note,
            "full_isolation": level == "full",
            "detail": {
                "mode": mode,
                "level": level,
                "available": available,
                "label": label,
                "note": note,
            },
        }
        if not available:
            from backend.core.config import settings

            exec_mode = str(getattr(settings, "agent_execution_mode", "sandbox") or "sandbox")
            if exec_mode == "sandbox":
                issues.append(describe_exit_reason("sandbox_missing"))
                actions.append(
                    {
                        "id": "sandbox_docs",
                        "label": "沙箱说明",
                        "hint": note
                        or "install bubblewrap / enable WSL Job; or agent_execution_mode=local",
                    }
                )
    except Exception as e:
        sandbox = {"ok": None, "error": str(e), "full_isolation": False, "level": "unknown"}

    # ── Budget policy visibility ──
    budget = {}
    try:
        from backend.core.config import settings

        hard = bool(getattr(settings, "agent_budget_hard_cap_only", False))
        soft = bool(getattr(settings, "agent_budget_soft_renew_enabled", False)) and not hard
        budget = {
            "hard_cap_only": hard,
            "soft_renew_enabled": soft,
            "soft_renew_max": int(getattr(settings, "agent_budget_soft_renew_max", 2) or 2),
            "soft_renew_min_add": int(
                getattr(settings, "agent_budget_soft_renew_min_add", 50_000) or 50_000
            ),
            "narrative": "hard_first" if hard or not soft else "soft_renew_active",
        }
        if soft and int(budget["soft_renew_max"]) > 2:
            issues.append(
                {
                    "code": "soft_renew_loose",
                    "severity": "warn",
                    "title": "Soft renew 偏松",
                    "message": (
                        f"soft_renew_max={budget['soft_renew_max']}（日用建议 ≤2）。"
                        "长任务请用 marathon profile，并在 UI 展示续额次数。"
                    ),
                    "recovery_hint": "设 agent_budget_soft_renew_max=2 或 hard_cap_only=true",
                }
            )
    except Exception:
        budget = {}

    # ── Court mode + escape hatches (red health when degraded) ──
    court = {}
    degraded_modes: list[dict[str, Any]] = []
    try:
        from backend.core.config import settings
        from backend.kernel.production_guard import is_dev_unsafe, is_production_guard
        import os

        rust_req = bool(getattr(settings, "agent_court_rust_required", True))
        prod = is_production_guard()
        dev_u = is_dev_unsafe()
        court = {
            "rust_required": rust_req,
            "python_tail_locked": prod and not dev_u,
            "production_guard": prod,
            "dev_unsafe": dev_u,
        }
        if dev_u:
            degraded_modes.append(
                {
                    "id": "dev_unsafe",
                    "severity": "error",
                    "title": "DEV_UNSAFE",
                    "message": "生产路径逃生口已打开（Python fallback / 弱守卫可能生效）",
                }
            )
        if not rust_req:
            degraded_modes.append(
                {
                    "id": "court_python",
                    "severity": "error",
                    "title": "Court 非 Rust 强制",
                    "message": "agent_court_rust_required=false — Court 可回落 Python",
                }
            )
        backend_env = (
            os.environ.get("TAKTON_KERNEL_BACKEND")
            or getattr(settings, "agent_kernel_backend", "")
            or ""
        ).strip().lower()
        if backend_env == "python":
            degraded_modes.append(
                {
                    "id": "kernel_python",
                    "severity": "error",
                    "title": "Kernel backend=python",
                    "message": "控制平面未走 Rust host",
                }
            )
        if not bool(getattr(settings, "agent_kernel_require_intent", True)):
            degraded_modes.append(
                {
                    "id": "require_intent_off",
                    "severity": "warn",
                    "title": "require_intent=false",
                    "message": "可回到弱 Intent 路径",
                }
            )
        for d in degraded_modes:
            issues.append(
                {
                    "code": d["id"],
                    "severity": d["severity"],
                    "title": d["title"],
                    "message": d["message"],
                    "recovery_hint": "生产 profile 关闭该开关；仅本地调试使用",
                }
            )
    except Exception:
        court = {}

    # Sandbox honesty: restricted ≠ full OS isolation
    try:
        level = sandbox.get("level")
        if level == "restricted":
            note = str(sandbox.get("note") or "Job/进程管控可用，无完整 FS 隔离")
            degraded_modes.append(
                {
                    "id": "sandbox_restricted",
                    "severity": "warn",
                    "title": "沙箱受限（非完整隔离）",
                    "message": note,
                }
            )
            issues.append(
                {
                    "code": "sandbox_restricted",
                    "severity": "warn",
                    "title": "沙箱受限模式",
                    "message": (
                        "当前不是完整 OS 沙箱（bwrap/seatbelt）。"
                        "进程可管，文件系统未隔离——勿误以为已进沙箱。"
                    ),
                    "recovery_hint": "Linux 安装 bubblewrap；Windows 装 WSL2+bwrap；或接受 restricted 风险",
                }
            )
    except Exception:
        pass

    severity = "ok"
    for i in issues:
        sev = str(i.get("severity") or "info")
        if sev == "error":
            severity = "error"
            break
        if sev == "warn" and severity != "error":
            severity = "warn"

    host_epoch = int(host_status.get("host_epoch") or 0)
    return {
        "ok": severity == "ok" and host_up and abi_ok,
        "severity": severity,
        "backend": backend if "backend" in dir() else host_status.get("backend"),
        "host": host_status,
        "host_epoch": host_epoch,
        "sandbox": sandbox,
        "budget": budget,
        "court": court,
        "degraded_modes": degraded_modes,
        "issues": issues,
        "actions": actions,
        "scenario": _default_scenario_hint(),
    }


def _default_scenario_hint() -> dict[str, Any]:
    try:
        from backend.core.config import settings

        return {
            "id": str(
                getattr(settings, "agent_default_scenario", "coding_research") or "coding_research"
            ),
            "coding_profile": str(
                getattr(settings, "agent_default_coding_profile", "engineering")
                or "engineering"
            ),
            "require_intent": bool(getattr(settings, "agent_kernel_require_intent", True)),
        }
    except Exception:
        return {"id": "coding_research"}


def try_restart_host() -> dict[str, Any]:
    """Operator/UI host restart. Always force-kill (cooldown does not skip).

    Must be run off the asyncio event loop (see host_restart_api) — this path
    does taskkill + wait + spawn and would freeze all HTTP/WS if called sync
    inside an async route.
    """
    try:
        from backend.kernel_rust.client import (
            restart_kernel_host,
            is_rust_host_available,
        )
        from backend.kernel import get_kernel

        # force=True: UI click must kill even when TCP port still "open" (hung host)
        ok = restart_kernel_host(force=True)
        k = get_kernel()
        # Do NOT reset_kernel_for_tests() here — that wipes the process-wide
        # singleton mid-flight and races agent loops. Just mark wipe + reconnect.
        if hasattr(k, "_mark_host_wiped"):
            try:
                k._mark_host_wiped()
            except Exception:
                pass
        try:
            if hasattr(k, "_rpc"):
                k._rpc.close()
            if hasattr(k, "_soft_reconnect"):
                k._soft_reconnect()
            elif hasattr(k, "_rpc"):
                k._rpc.connect()
        except Exception as re:
            # Host may still be coming up; client will reconnect on next RPC
            logger = __import__("logging").getLogger(__name__)
            logger.debug("post-restart reconnect: %s", re)
        if ok or is_rust_host_available():
            try:
                if hasattr(k, "_assert_abi_or_fail"):
                    k._assert_abi_or_fail()
            except Exception as abi_e:
                return {
                    "ok": True,
                    "warning": f"host up but ABI check: {abi_e}",
                    "host": {},
                }
            st = k.host_runtime_status() if hasattr(k, "host_runtime_status") else {}
            return {"ok": True, "host": st}
        return {"ok": False, "error": "restart failed — host not accepting connections"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
