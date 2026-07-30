"""Runtime 状态：供托盘/CLI 轻量探测（本机 loopback 友好）。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runtime", tags=["runtime"])


def _is_loopback(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1", "localhost")


@router.get("/status")
async def runtime_status(request: Request) -> dict[str, Any]:
    """Kernel Host 心跳 + 粗粒度负载。

    完整明细仍走鉴权 API；本接口供托盘/脚本探测「活着」。
    loopback 时附加 jobs/approvals 计数（单用户桌面）。
    """
    out: dict[str, Any] = {
        "ok": True,
        "role": "kernel_host",
        "product": "takton-aios",
    }
    try:
        from backend.core.config import settings

        out["aios_profile"] = getattr(settings, "aios_profile", "") or ""
        out["dispatcher_enabled"] = bool(getattr(settings, "agent_dispatcher_enabled", True))
    except Exception:
        pass

    if not _is_loopback(request):
        return out

    try:
        from backend.kernel import get_kernel
        from backend.kernel.workforce import get_workforce_inbox

        kernel = get_kernel()
        live = list(kernel.list_processes(include_terminal=False))
        out["processes_live"] = len(live)
        esc = kernel.list_escalations(status="pending")
        out["approvals_pending"] = len(esc)
        inbox = get_workforce_inbox()
        if inbox is not None:
            claimed = await inbox.list_items(status="claimed", limit=50)
            pending = await inbox.list_items(status="pending", limit=50)
            out["jobs_claimed"] = len(claimed)
            out["jobs_pending"] = len(pending)
        out["badge"] = int(out.get("jobs_claimed") or 0) + int(out.get("approvals_pending") or 0)
    except Exception as e:
        logger.debug("runtime status detail: %s", e)
        out["detail_error"] = str(e)[:120]
    return out
