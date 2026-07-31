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
                host_status = {"up": True, "abi": abi, "methods_count": len(methods)}
            except Exception as e:
                host_up = False
                host_status = {"up": False, "error": str(e)}
        else:
            backend = "python"
            host_status = {"up": False, "backend": "python"}
    except Exception as e:
        backend = "unknown"
        host_status = {"up": False, "error": str(e)}
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
    if host_up and not abi_ok and not any(i.get("code") == "host_abi_mismatch" for i in issues):
        issues.append(describe_exit_reason("host_abi_mismatch"))
        actions.append(
            {
                "id": "rebuild_host",
                "label": "重建并 stage Host",
                "hint": "node scripts/ensure-vendor-host.mjs",
            }
        )

    # ── Sandbox capability ──
    sandbox: dict[str, Any] = {"ok": True}
    try:
        from backend.computer.detect import detect_sandbox_capability

        cap = detect_sandbox_capability()
        # dataclass or dict
        available = bool(
            getattr(cap, "available", None)
            or getattr(cap, "ok", None)
            or (isinstance(cap, dict) and (cap.get("available") or cap.get("ok")))
        )
        backend = getattr(cap, "backend", None) or (
            cap.get("backend") if isinstance(cap, dict) else None
        )
        sandbox = {
            "ok": available,
            "backend": backend,
            "detail": cap if isinstance(cap, dict) else getattr(cap, "__dict__", str(cap)),
        }
        if not available:
            from backend.core.config import settings

            mode = str(getattr(settings, "agent_execution_mode", "sandbox") or "sandbox")
            if mode == "sandbox":
                issues.append(describe_exit_reason("sandbox_missing"))
                actions.append(
                    {
                        "id": "sandbox_docs",
                        "label": "沙箱说明",
                        "hint": "install bubblewrap / enable WSL Job; or agent_execution_mode=local",
                    }
                )
    except Exception as e:
        sandbox = {"ok": None, "error": str(e)}

    # ── Budget policy visibility ──
    budget = {}
    try:
        from backend.core.config import settings

        hard = bool(getattr(settings, "agent_budget_hard_cap_only", False))
        soft = bool(getattr(settings, "agent_budget_soft_renew_enabled", True)) and not hard
        budget = {
            "hard_cap_only": hard,
            "soft_renew_enabled": soft,
            "soft_renew_max": int(getattr(settings, "agent_budget_soft_renew_max", 12) or 12),
        }
    except Exception:
        budget = {}

    # ── Court mode ──
    court = {}
    try:
        from backend.core.config import settings
        from backend.kernel.production_guard import is_dev_unsafe, is_production_guard

        court = {
            "rust_required": bool(getattr(settings, "agent_court_rust_required", True)),
            "python_tail_locked": is_production_guard() and not is_dev_unsafe(),
            "production_guard": is_production_guard(),
        }
    except Exception:
        court = {}

    severity = "ok"
    for i in issues:
        sev = str(i.get("severity") or "info")
        if sev == "error":
            severity = "error"
            break
        if sev == "warn" and severity != "error":
            severity = "warn"

    return {
        "ok": severity == "ok" and host_up and abi_ok,
        "severity": severity,
        "backend": backend if "backend" in dir() else host_status.get("backend"),
        "host": host_status,
        "sandbox": sandbox,
        "budget": budget,
        "court": court,
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
    try:
        from backend.kernel_rust.client import restart_kernel_host, is_rust_host_available
        from backend.kernel import reset_kernel_for_tests, get_kernel

        ok = restart_kernel_host()
        reset_kernel_for_tests()
        if ok or is_rust_host_available():
            k = get_kernel()
            st = k.host_runtime_status() if hasattr(k, "host_runtime_status") else {}
            return {"ok": True, "host": st}
        return {"ok": False, "error": "restart failed"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
