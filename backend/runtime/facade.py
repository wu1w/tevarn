"""Runtime Services 门面：编制 / 工单 / 调度。

Kernel 控制平面默认由 **Rust** ``tevarn-kernel-host`` 提供（经 ``get_kernel()``）。
Workforce 装配（Inbox / Dispatcher / Evolution）仍由 Python 适配器完成，
并注册到 Rust Runtime 的 service registry（若 host 可用）。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_kernel() -> Any:
    from backend.kernel import get_kernel as _gk

    return _gk()


def get_kernel_backend() -> str:
    try:
        from backend.kernel.kernel import get_kernel_backend as _gb

        return _gb()
    except Exception:
        return "unknown"


def init_workforce(kernel: Any, session_factory: Any, settings: Any) -> tuple[Any, Any]:
    from backend.kernel.workforce import init_workforce as _init

    inbox, dispatcher = _init(kernel, session_factory, settings)
    # 向 Rust runtime 注册服务元数据（可选）
    try:
        if hasattr(kernel, "_call"):
            kernel._call(
                "register_service",
                {
                    "name": "workforce",
                    "meta": {
                        "inbox": inbox is not None,
                        "dispatcher": dispatcher is not None,
                    },
                },
            )
    except Exception as e:
        logger.debug("register workforce service on rust runtime: %s", e)
    return inbox, dispatcher


def get_workforce_inbox() -> Any:
    from backend.kernel.workforce import get_workforce_inbox as _g

    return _g()


def get_workforce_dispatcher() -> Any:
    from backend.kernel.workforce import get_workforce_dispatcher as _g

    return _g()


def build_daily_report(kernel: Any, inbox: Any, **kwargs: Any) -> Any:
    from backend.kernel.workforce import build_daily_report as _b

    return _b(kernel, inbox, **kwargs)


def runtime_health() -> dict[str, Any]:
    """Kernel/Runtime 健康快照（Rust host 时含 resource 视图）。"""
    k = get_kernel()
    backend = get_kernel_backend()
    out: dict[str, Any] = {"backend": backend}
    if hasattr(k, "health"):
        try:
            out.update(k.health() or {})
        except Exception as e:
            out["health_error"] = str(e)
    else:
        try:
            procs = k.list_processes(include_terminal=False)
            chain = k.verify_event_chain()
            out.update(
                {
                    "ok": True,
                    "live_processes": len(procs),
                    "audit_chain_ok": bool(chain[0]) if isinstance(chain, tuple) else None,
                }
            )
        except Exception as e:
            out["ok"] = False
            out["error"] = str(e)
    return out
