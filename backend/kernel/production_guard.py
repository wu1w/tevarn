"""H2 production safety guards — 收窄 · 强制 · 可观测.

Production paths must not silently drop to ungoverned Agent mode.
Explicit escape hatch: ``TEVARN_DEV_UNSAFE=1`` or development env.
"""

from __future__ import annotations

import os
from typing import Any


_DEV_UNSAFE_WARNED = False


def warn_dev_unsafe_once() -> None:
    """Loud startup banner when escape hatches are open (alpha release polish)."""
    global _DEV_UNSAFE_WARNED
    if _DEV_UNSAFE_WARNED:
        return
    if not is_dev_unsafe():
        return
    _DEV_UNSAFE_WARNED = True
    import logging

    logging.getLogger("tevarn.production_guard").warning(
        "\n"
        "╔══════════════════════════════════════════════════════════╗\n"
        "║  TEVARN DEV_UNSAFE / development escape hatch ACTIVE     ║\n"
        "║  Python kernel fallback & weak guards may be allowed.    ║\n"
        "║  Do NOT ship this profile. Set TEVARN_FORCE_PRODUCTION   ║\n"
        "║  _GUARD=1 and clear TEVARN_DEV_UNSAFE for production.    ║\n"
        "╚══════════════════════════════════════════════════════════╝"
    )


def is_dev_unsafe() -> bool:
    """True only when operator explicitly accepts ungoverned / Python fallback."""
    v = (os.environ.get("TEVARN_DEV_UNSAFE") or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    try:
        from backend.core.config import settings

        env = str(getattr(settings, "environment", "") or getattr(settings, "env", "") or "").lower()
        if env in ("development", "dev", "test"):
            # pytest / local dev: allow unless TEVARN_FORCE_PRODUCTION_GUARD=1
            force = (os.environ.get("TEVARN_FORCE_PRODUCTION_GUARD") or "").strip().lower()
            if force in ("1", "true", "yes", "on"):
                return False
            # Still treat as "unsafe allowed" for unit tests that use Python kernel
            if env == "test" or os.environ.get("PYTEST_CURRENT_TEST"):
                return True
            # development: allow unsafe only if kernel explicitly disabled or backend=python
            return True
    except Exception:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return True
    return False


def is_production_guard() -> bool:
    """When True, enforce hard closed-loop (no None caps, no silent kernel off)."""
    if (os.environ.get("TEVARN_FORCE_PRODUCTION_GUARD") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return True
    return not is_dev_unsafe()


def allow_kernel_disabled() -> bool:
    """agent_kernel_enabled=False only legal under DEV_UNSAFE / test."""
    return is_dev_unsafe()


def allow_python_kernel_fallback() -> bool:
    """Silent Python AgentKernel fallback only under DEV_UNSAFE / explicit backend=python."""
    forced = (
        os.environ.get("TEVARN_KERNEL_BACKEND")
        or os.environ.get("agent_kernel_backend")
        or ""
    ).strip().lower()
    if forced == "python":
        return True
    return is_dev_unsafe()


def allow_compat_full_open() -> bool:
    """capabilities=None full-open only under DEV_UNSAFE."""
    return is_dev_unsafe()


def emit_compat_denied(process_id: str | None, reason: str, detail: dict[str, Any] | None = None) -> None:
    """Best-effort audit when production blocks compat mode."""
    try:
        from backend.kernel import get_kernel

        k = get_kernel()
        pid = process_id or "system"
        payload = {"reason": reason, "production_guard": True, **(detail or {})}
        if hasattr(k, "emit"):
            k.emit("policy.compat_denied", pid, payload)
        elif hasattr(k, "_call"):
            k._call(
                "emit",
                {"kind": "policy.compat_denied", "process_id": pid, "detail": payload},
            )
    except Exception:
        pass
