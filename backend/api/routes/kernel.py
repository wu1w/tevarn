"""Agent Kernel 观测 API（阶段 1/W2）。

Security Console 数据源：当前进程树（能力/预算/状态）+ 中介审计事件。
只读接口——进程生命周期由 loop 驱动，不接受外部写操作。

0.4.1：新增提权交互端点（escalations）——这是控制台唯一的写入口，
对应「用户授权是唯一合法的能力扩大通道」。
"""

from __future__ import annotations

import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.kernel import get_kernel
from backend.schemas.user import UserRead

from ..dependencies import get_current_user, require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/kernel", tags=["kernel"])

_REPORT_READ_KEY = "workforce_report_read_at"


async def _kcall(method: str, params: dict[str, Any] | None = None) -> Any:
    """异步 RPC：优先 _acall，否则 to_thread(_call)，避免阻塞事件循环（P1）。"""
    import asyncio

    k = get_kernel()
    acall = getattr(k, "_acall", None)
    if acall is not None:
        return await acall(method, params)
    call = getattr(k, "_call", None)
    if call is not None:
        return await asyncio.to_thread(call, method, params)
    raise RuntimeError(f"kernel has no RPC for {method}")


class StopJobBody(BaseModel):
    """E4 统一停止：至少填 inbox_item_id 或 process_id。"""

    inbox_item_id: str | None = Field(None, description="工单 id")
    process_id: str | None = Field(None, description="kernel process id")
    reason: str = Field("stopped by user", max_length=500)


@router.get("/processes")
async def list_processes(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    include_terminal: bool = Query(False),
):
    """进程列表。

    shared_state=True 时：内存进程 + DB 档案合并（多 worker 观测前提）。
    本 worker 内存中的 live 进程优先（状态更鲜活）。
    """
    import asyncio

    kernel = get_kernel()
    # Off event loop: kernel RPC is sync and must not freeze UI polls during CEO runs.
    procs = await asyncio.to_thread(kernel.list_processes, include_terminal=True)
    live = {p.id: p.to_dict() for p in procs}
    shared = True
    try:
        from backend.core.config import settings
        shared = bool(getattr(settings, "agent_kernel_shared_state", True))
    except Exception:
        shared = True

    db_error = None
    if shared:
        try:
            from sqlalchemy import select

            from backend.database import AsyncSessionLocal
            from backend.models.agent_identity import KernelProcessRecord

            async with AsyncSessionLocal() as session:
                rows = (await session.execute(select(KernelProcessRecord))).scalars().all()
            for r in rows:
                if r.process_id in live:
                    continue  # 内存优先
                if not include_terminal and r.state in (
                    "completed", "failed", "killed", "interrupted", "exited", "done",
                ):
                    continue
                live[r.process_id] = {
                    "id": r.process_id,
                    "identity": r.identity_key,
                    "session_id": r.session_id,
                    "parent_id": r.parent_process_id,
                    "capabilities": r.capabilities,
                    "token_budget": r.token_budget,
                    "tokens_used": r.tokens_used or 0,
                    "state": r.state,
                    "created_at": r.started_at or 0,
                    "started_at": r.started_at,
                    "ended_at": r.ended_at,
                    "exit_reason": r.exit_reason,
                    "source": "db",
                }
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("list_processes DB merge failed: %s", e)
            db_error = str(e)

    procs = list(live.values())
    if not include_terminal:
        terminal = {"completed", "failed", "killed", "interrupted", "exited", "done", "error"}
        procs = [p for p in procs if p.get("state") not in terminal]
    # 标记来源：live 优先；DB 档案若与 live 终态冲突，标 db_stale
    for p in procs:
        if p.get("source") != "db":
            p["source"] = p.get("source") or "live"
            p["authoritative"] = True
        else:
            p["authoritative"] = False
    # P2：stalled 检测 — running 且长时间无 charge 心跳
    import time as _time

    try:
        from backend.core.config import settings as _st

        stall_sec = float(getattr(_st, "agent_process_stall_seconds", 300) or 300)
    except Exception:
        stall_sec = 300.0
    now = _time.time()
    for p in procs:
        st = str(p.get("state") or "")
        if st not in ("running", "waiting", "suspended"):
            p["stalled"] = False
            continue
        meta = p.get("meta") if isinstance(p.get("meta"), dict) else {}
        last = meta.get("last_charge_at") if isinstance(meta, dict) else None
        started = p.get("started_at") or p.get("created_at") or 0
        try:
            last_f = float(last) if last is not None else float(started or 0)
        except (TypeError, ValueError):
            last_f = float(started or 0)
        idle = now - last_f if last_f > 0 else 0
        p["stalled"] = bool(idle >= stall_sec and st == "running")
        p["idle_seconds"] = int(idle)
    # 多用户：过滤非本人进程（single_user 全放行）
    # 过滤基础设施失败时 fail-closed（503），禁止退回全量列表
    from backend.core.config import settings as _st2

    if not bool(getattr(_st2, "single_user_mode", True)):
        try:
            from backend.kernel.process_access import assert_user_owns_process
        except Exception as e:
            logger.error("list_processes ownership filter import failed: %s", e)
            raise HTTPException(
                status_code=503,
                detail="process ownership filter unavailable",
            ) from e
        filtered = []
        for p in procs:
            try:
                await assert_user_owns_process(
                    kernel, str(p.get("id") or ""), current_user.id
                )
                filtered.append(p)
            except (ValueError, PermissionError):
                continue
            except Exception as e:
                # 单条异常不拖垮整表，但也不静默放行该条
                logger.debug(
                    "list_processes skip process %s: %s",
                    str(p.get("id") or "")[:12],
                    e,
                )
                continue
        procs = filtered

    out = {
        "enabled": True,
        "processes": procs,
        "total": len(procs),
        "shared_state": shared,
        "stall_threshold_seconds": stall_sec,
        "live_preferred": True,
    }
    if db_error:
        out["db_warning"] = db_error
        out["db_stale_risk"] = True
    return out


@router.get("/processes/tree")
async def process_tree(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    include_terminal: bool = Query(False),
):
    """进程树：按 parent_id 建树，附能力/预算继承摘要（分析 P1）。

    多用户与 list_processes 同口径过滤；filter 基础设施失败 → 503。
    """
    kernel = get_kernel()
    flat = [p.to_dict() for p in kernel.list_processes(include_terminal=include_terminal)]
    from backend.core.config import settings as _st_tree

    if not bool(getattr(_st_tree, "single_user_mode", True)):
        try:
            from backend.kernel.process_access import assert_user_owns_process
        except Exception as e:
            logger.error("process_tree ownership filter import failed: %s", e)
            raise HTTPException(
                status_code=503,
                detail="process ownership filter unavailable",
            ) from e
        owned: list[dict[str, Any]] = []
        for p in flat:
            try:
                await assert_user_owns_process(
                    kernel, str(p.get("id") or ""), current_user.id
                )
                owned.append(p)
            except (ValueError, PermissionError):
                continue
            except Exception as e:
                logger.debug(
                    "process_tree skip %s: %s", str(p.get("id") or "")[:12], e
                )
                continue
        flat = owned
    by_id: dict[str, dict[str, Any]] = {}
    for p in flat:
        pid = str(p.get("id") or "")
        if not pid:
            continue
        p = dict(p)
        p["children"] = []
        caps = p.get("capabilities")
        p["caps_count"] = len(caps) if isinstance(caps, list) else (0 if caps is None else 1)
        p["compat_open"] = caps is None
        meta = p.get("meta") if isinstance(p.get("meta"), dict) else {}
        p["soft_renew_count"] = int(meta.get("soft_renew_count") or 0)
        p["tools_visible_count"] = meta.get("tools_visible_count")
        by_id[pid] = p
    roots: list[dict[str, Any]] = []
    for _pid, p in by_id.items():
        parent = p.get("parent_id")
        if parent and str(parent) in by_id:
            by_id[str(parent)]["children"].append(p)
        else:
            roots.append(p)

    def _sort(nodes: list[dict[str, Any]]) -> None:
        nodes.sort(key=lambda x: float(x.get("created_at") or 0))
        for n in nodes:
            ch = n.get("children") or []
            if isinstance(ch, list):
                _sort(ch)

    _sort(roots)
    return {
        "roots": roots,
        "total": len(by_id),
        "flat_count": len(flat),
    }


@router.get("/governance/status")
async def governance_status(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """H2 治理基线快照：旁路开关、soft renew、密钥来源、生产守卫。"""
    from backend.core.config import settings
    from backend.kernel import get_kernel_backend
    from backend.kernel.production_guard import (
        allow_compat_full_open,
        allow_kernel_disabled,
        allow_python_kernel_fallback,
        is_dev_unsafe,
        is_production_guard,
    )

    hmac_src = "unknown"
    try:
        from backend.kernel.signing import hmac_key_source

        hmac_src = hmac_key_source()
    except Exception:
        pass
    hard_only = bool(getattr(settings, "agent_budget_hard_cap_only", False))
    soft_on = bool(getattr(settings, "agent_budget_soft_renew_enabled", False)) and not hard_only
    return {
        "production_guard": is_production_guard(),
        "dev_unsafe": is_dev_unsafe(),
        "kernel_backend": get_kernel_backend(),
        "kernel_enabled": bool(getattr(settings, "agent_kernel_enabled", True)),
        "require_intent": bool(getattr(settings, "agent_kernel_require_intent", True)),
        "allow_compat_full_open": allow_compat_full_open(),
        "allow_python_fallback": allow_python_kernel_fallback(),
        "allow_kernel_disabled": allow_kernel_disabled(),
        "soft_renew_enabled": soft_on,
        "hard_cap_only": hard_only,
        "soft_renew_max": int(getattr(settings, "agent_budget_soft_renew_max", 2) or 2),
        "token_hmac_source": hmac_src,
        "court_rust_required": bool(getattr(settings, "agent_court_rust_required", True)),
        "run_gate_required": bool(getattr(settings, "agent_kernel_run_gate_required", True)),
    }


@router.get("/sys/memory/{identity}")
async def sys_memory_list_api(
    identity: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """内核风格 Memory 服务：list keys（经 host RPC）。"""
    k = get_kernel()
    if hasattr(k, "_call"):
        return await k._acall("sys_memory_list", {"identity": identity}) or {}
    return {"identity": identity, "keys": []}


@router.post("/sys/memory/{identity}")
async def sys_memory_put_api(
    identity: str,
    body: dict[str, Any],
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    k = get_kernel()
    key = str(body.get("key") or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="key required")
    if hasattr(k, "_call"):
        return (
            await k._acall(
                "sys_memory_put",
                {"identity": identity, "key": key, "value": body.get("value")},
            )
            or {}
        )
    raise HTTPException(status_code=503, detail="kernel host required")


@router.get("/sys/memory-layers/{identity}")
async def memory_layers_api(
    identity: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    layer: str | None = Query(None),
):
    """分层记忆列表（可审计内核风格接口）。"""
    k = get_kernel()
    if hasattr(k, "_call"):
        params: dict[str, Any] = {"identity": identity}
        if layer:
            params["layer"] = layer
        return await k._acall("memory_layer_list", params) or {}
    return {"entries": []}


@router.get("/sys/context/{process_id}")
async def context_status_api(
    process_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """上下文 VM 状态（配额/页/隔离）。"""
    k = get_kernel()
    if hasattr(k, "_call"):
        pages = await k._acall("context_list_pages", {"process_id": process_id}) or {}
        st = await k._acall("context_status", {"process_id": process_id}) or {}
        return {"process_id": process_id, "pages": pages, "status": st}
    return {"process_id": process_id, "pages": {}, "status": {}}


@router.post("/sys/context/schedule")
async def context_schedule_api(
    body: dict[str, Any],
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """内核调度 tick：LRU 换出 / 冷页老化。"""
    k = get_kernel()
    if not hasattr(k, "_call"):
        raise HTTPException(status_code=503, detail="kernel host required")
    pid = body.get("process_id")
    return await k._acall("context_schedule", {"process_id": pid} if pid else {}) or {}


@router.post("/sys/memory/schedule")
async def memory_schedule_api(
    body: dict[str, Any],
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """记忆调度：consolidate + working GC。"""
    k = get_kernel()
    if not hasattr(k, "_call"):
        raise HTTPException(status_code=503, detail="kernel host required")
    identity = body.get("identity")
    return (
        await k._acall("memory_layer_schedule", {"identity": identity} if identity else {})
        or {}
    )


@router.get("/device-sync/status")
async def device_sync_status_api(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    k = get_kernel()
    if hasattr(k, "_call"):
        return await k._acall("device_sync_status") or {}
    return {}


@router.get("/device-sync/devices")
async def device_sync_list_api(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    k = get_kernel()
    if hasattr(k, "_call"):
        return await k._acall("device_sync_list") or {}
    return {"devices": []}


@router.post("/device-sync/push")
async def device_sync_push_api(
    body: dict[str, Any],
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    k = get_kernel()
    if not hasattr(k, "_call"):
        raise HTTPException(status_code=503, detail="kernel host required")
    return (
        await k._acall(
            "device_sync_push",
            {
                "identity": body.get("identity") or "main",
                "to_device": body.get("to_device"),
            },
        )
        or {}
    )


@router.post("/device-sync/pull")
async def device_sync_pull_api(
    body: dict[str, Any],
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    k = get_kernel()
    if not hasattr(k, "_call"):
        raise HTTPException(status_code=503, detail="kernel host required")
    return (
        await k._acall(
            "device_sync_pull",
            {
                "identity": body.get("identity") or "main",
                "since_revision": body.get("since_revision"),
            },
        )
        or {}
    )


@router.post("/device-sync/apply")
async def device_sync_apply_api(
    body: dict[str, Any],
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    k = get_kernel()
    if not hasattr(k, "_call"):
        raise HTTPException(status_code=503, detail="kernel host required")
    env = body.get("envelope") or body
    return await k._acall("device_sync_apply", {"envelope": env}) or {}


@router.get("/audit/anchor")
async def audit_anchor_api(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """WORM 外部锚点状态 + verify。"""
    k = get_kernel()
    if hasattr(k, "_call"):
        return await k._acall("audit_anchor_status") or {}
    try:
        from backend.kernel.audit_store import AuditEventStore

        return AuditEventStore().verify_anchor()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/runtime/health")
async def runtime_health_api(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """默认路径健康：host/ABI/沙箱/预算/court — 主 UI 恢复卡片数据源。"""
    import asyncio

    from backend.services.runtime_health import collect_runtime_health

    return await asyncio.to_thread(collect_runtime_health)


@router.get("/runtime/endpoints")
async def runtime_endpoints_api(
    request: Request,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """前端 WS/API 基址发现：避免 dev 写死 8090。

    浏览器可据此拼 ws URL；Electron 仍可优先 __TEVARN_WS_URL__。
    """
    import os

    from backend.core.config import settings

    host = request.headers.get("host") or "127.0.0.1:8090"
    # 真实后端端口优先级：
    # 1) 本请求实际到达的端口（uvicorn 监听端口，最准）
    # 2) TEVARN_API_PORT / PORT
    # 3) settings.app_port（默认 8090）
    # 注意：settings 若仍为旧默认 8000，而进程在 8090，必须用 request 端口纠正
    req_port = None
    try:
        req_port = int(request.url.port) if request.url.port else None
    except Exception:
        req_port = None
    if not req_port and ":" in str(host):
        try:
            req_port = int(str(host).rsplit(":", 1)[-1])
        except Exception:
            req_port = None
    env_port = os.environ.get("TEVARN_API_PORT") or os.environ.get("PORT")
    api_port = int(
        req_port
        or env_port
        or getattr(settings, "app_port", 0)
        or 8090
    )
    env_ws = (os.environ.get("TEVARN_PUBLIC_WS_URL") or "").strip().rstrip("/")
    env_api = (os.environ.get("TEVARN_PUBLIC_API_URL") or "").strip().rstrip("/")
    # Next dev 不代理 WS upgrade → 默认直连后端
    default_api = f"http://127.0.0.1:{api_port}/api"
    default_ws = f"ws://127.0.0.1:{api_port}/api"
    host_epoch = 0
    try:
        from backend.kernel import get_kernel

        k = get_kernel()
        host_epoch = int(getattr(k, "_host_epoch", 0) or 0)
    except Exception:
        pass
    return {
        "api_base": env_api or default_api,
        "ws_base": env_ws or default_ws,
        "api_port": api_port,
        "request_host": host,
        "host_epoch": host_epoch,
    }


@router.post("/host/restart")
async def host_restart_api(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """一键重启 Kernel Host（卡死/无响应恢复）。

    必须在线程池执行：内部 taskkill + 等端口释放会阻塞数秒；若在 event loop
    同步跑，整站 HTTP/WS 一起假死（前端点「重启」后卡死）。
    """
    import asyncio

    from backend.services.runtime_health import try_restart_host

    return await asyncio.to_thread(try_restart_host)


class ConfirmResolveBody(BaseModel):
    approved: bool = False
    scope: str = Field("once", pattern="^(once|session|agent|deny)$")
    # clarify 选项原文（可选）
    choice: str | None = None


@router.post("/confirm/{confirm_id}")
async def confirm_resolve_http(
    confirm_id: str,
    body: ConfirmResolveBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """危险确认 / clarify HTTP 兜底：WS sender 未注册时前端可 POST 此路径。

    校验 pending.user_id == current_user，防止横向 resolve。
    过期/不存在 → 410；归属不符 → 403。
    """
    from backend.services import confirm_manager

    # 先探归属（不消费）
    pending = getattr(confirm_manager, "_pending", {}).get(confirm_id)
    if pending is None:
        raise HTTPException(status_code=410, detail="confirm expired or not found")
    _ev, holder = pending
    owner = str(holder.get("user_id") or "").strip()
    if owner:
        if str(current_user.id) != owner:
            raise HTTPException(status_code=403, detail="confirm not owned by current user")
    else:
        # multi-user 无主 pending：拒绝（与 resolve_confirmation fail-closed 一致）
        from backend.core.config import settings as _st_confirm

        if not bool(getattr(_st_confirm, "single_user_mode", True)):
            raise HTTPException(
                status_code=403,
                detail="confirm has no owner binding (multi-user)",
            )

    scope = str(body.scope or "once").lower()
    if scope not in ("once", "session", "agent", "deny"):
        scope = "deny" if not body.approved else "once"
    approved = bool(body.approved)
    choice = (body.choice or "").strip() or None
    # 预检 options 白名单（给出 400 而非 410，便于前端区分）
    opt_raw = holder.get("options")
    if not isinstance(opt_raw, list):
        payload = holder.get("payload") if isinstance(holder.get("payload"), dict) else {}
        opt_raw = payload.get("options") if isinstance(payload, dict) else None
    if choice:
        opts = [str(o).strip() for o in (opt_raw or []) if str(o or "").strip()]
        if opts and choice not in opts:
            raise HTTPException(
                status_code=400,
                detail="choice must be one of the pending options",
            )
        if not opts and not approved:
            raise HTTPException(
                status_code=400,
                detail="free-form choice not allowed without options",
            )
        if opts:
            approved = True

    ok = confirm_manager.resolve_confirmation(
        confirm_id,
        approved,
        scope=scope,
        user_id=str(current_user.id),
        choice=choice,
    )
    if not ok:
        raise HTTPException(status_code=410, detail="confirm expired or not found")
    return {"ok": True, "confirm_id": confirm_id, "choice": choice}


@router.get("/host/watchdog")
async def host_watchdog_api(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """Host 存活 ping（含自动恢复计数）。"""
    k = get_kernel()
    if hasattr(k, "host_watchdog_ping"):
        return k.host_watchdog_ping()
    if hasattr(k, "_call"):
        try:
            return {"ok": True, "pong": await k._acall("ping") or {}}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "no host client"}


async def _require_process_owner(process_id: str, current_user: UserRead, *, live: bool = False):
    """归属校验：不通过则 HTTPException。"""
    from backend.kernel.process_access import (
        assert_user_owns_process,
        ownership_http_exc,
    )

    kernel = get_kernel()
    try:
        return await assert_user_owns_process(
            kernel, process_id, getattr(current_user, "id", current_user), require_live=live
        )
    except (ValueError, PermissionError) as e:
        code, detail = ownership_http_exc(e)
        raise HTTPException(status_code=code, detail=detail) from e


async def _require_identity_owner(identity_id: str, current_user: UserRead):
    """编制身份归属校验：不通过则 HTTPException。"""
    from backend.kernel.process_access import (
        assert_user_owns_identity,
        ownership_http_exc,
    )

    try:
        return await assert_user_owns_identity(
            identity_id, getattr(current_user, "id", current_user)
        )
    except (ValueError, PermissionError) as e:
        code, detail = ownership_http_exc(e)
        raise HTTPException(status_code=code, detail=detail) from e


@router.get("/processes/{process_id}")
async def get_process(
    process_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    await _require_process_owner(process_id, current_user)
    kernel = get_kernel()
    proc = kernel.get_process(process_id)
    if proc is None:
        raise HTTPException(status_code=404, detail=f"process not found: {process_id}")
    from backend.kernel.process_access import process_public_dict

    return process_public_dict(proc)


@router.post("/processes/{process_id}/suspend")
async def suspend_process(
    process_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    reason: str = Query("", description="挂起原因"),
):
    """Phase 3.3：挂起运行中进程（loop 下一轮 gate 阻塞）。"""
    await _require_process_owner(process_id, current_user, live=True)
    kernel = get_kernel()
    try:
        proc = await kernel.suspend_process(process_id, reason=reason or "")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "process": proc.to_dict()}


@router.post("/processes/{process_id}/resume")
async def resume_process(
    process_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """Phase 3.3：恢复挂起进程。"""
    await _require_process_owner(process_id, current_user)
    kernel = get_kernel()
    try:
        proc = await kernel.resume_process(process_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "process": proc.to_dict()}


@router.post("/processes/{process_id}/budget/top-up")
async def top_up_process_budget(
    process_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """CEO 运行中动态追加 token 预算。

    body: { amount: int (>0), reason?: str }
    下一刀 charge_tokens 立即使用新上限；不重置已用额度。
    """
    await _require_process_owner(process_id, current_user)
    kernel = get_kernel()
    try:
        amount = int(body.get("amount") or 0)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail="amount 必须为整数") from e
    reason = str(body.get("reason") or "").strip()
    try:
        result = kernel.top_up_budget(
            process_id,
            amount,
            by=f"ceo:{getattr(current_user, 'id', current_user)}",
            reason=reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # 同步关联 AgentRun.token_limit（若 process.meta.run_id 存在）
    try:
        proc = kernel.get_process(process_id)
        rid = (proc.meta or {}).get("run_id") if proc else None
        if rid and result.get("token_budget") is not None:
            import uuid as _uuid

            from backend.repositories.agent_run_repo import AsyncAgentRunRepository

            await AsyncAgentRunRepository().update_run(
                _uuid.UUID(str(rid)),
                {"token_limit": int(result["token_budget"])},
            )
    except Exception:
        pass
    return result


@router.post("/identities/{identity_id}/budget/top-up-running")
async def top_up_identity_running_budget(
    identity_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """对某员工所有运行中进程追加预算（CEO 一键加钱）。

    body: { amount: int, reason?: str, also_default?: bool }
    also_default=true 时同时抬高 identity.default_token_budget（后续新工单生效）。

    多用户下需拥有该 identity；在跑进程再走 process owner 校验（与单进程 top-up 一致）。
    """
    # 身份归属：与 process top-up 同一套 owner 语义
    await _require_identity_owner(identity_id, current_user)
    kernel = get_kernel()
    try:
        amount = int(body.get("amount") or 0)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail="amount 必须为整数") from e
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount 必须为正")
    reason = str(body.get("reason") or "").strip()
    key = f"wf:{identity_id}"
    live = kernel.live_processes_for_identity(key)
    # 兼容非 wf 前缀
    if not live:
        live = kernel.live_processes_for_identity(str(identity_id))
    results = []
    for p in live:
        try:
            # 双保险：每条在跑进程也过 process owner（防 identity 串绑）
            await _require_process_owner(p.id, current_user)
            results.append(
                kernel.top_up_budget(
                    p.id,
                    amount,
                    by=f"ceo:{getattr(current_user, 'id', current_user)}",
                    reason=reason or f"identity top-up {identity_id[:8]}",
                )
            )
        except HTTPException as e:
            results.append(
                {
                    "ok": False,
                    "process_id": getattr(p, "id", None),
                    "error": str(e.detail)[:200],
                }
            )
        except Exception as e:
            results.append({"ok": False, "process_id": p.id, "error": str(e)[:200]})

    default_updated = None
    if body.get("also_default"):
        reg = _identity_registry()
        if reg is not None:
            try:
                ident = await reg.get(identity_id)
                if ident is not None:
                    cur = int(getattr(ident, "default_token_budget", 0) or 0)
                    new_def = cur + amount if cur > 0 else amount
                    await reg.update_profile(
                        identity_id,
                        default_token_budget=new_def,
                    )
                    default_updated = new_def
            except Exception as e:
                default_updated = f"error:{e}"

    return {
        "ok": True,
        "identity_id": identity_id,
        "amount": amount,
        "processes": results,
        "count": len(results),
        "default_token_budget": default_updated,
    }


@router.get("/events")
async def list_events(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    process_id: str | None = Query(None),
    kind: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    kernel = get_kernel()
    events = kernel.events(process_id=process_id, kind=kind, limit=limit)
    return {
        "events": [e.to_dict() for e in events],
        "total": len(events),
    }


# ── 提权交互（0.4.1）──────────────────────────────────────────


@router.get("/escalations")
async def list_escalations(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    status: str | None = Query(None),
):
    """提权列表。shared_state 时合并 DB 中的 pending（跨 worker 可见）。"""
    import asyncio

    kernel = get_kernel()
    esc_list = await asyncio.to_thread(lambda: kernel.list_escalations(status=None))
    by_id = {r.id: r.to_dict() for r in esc_list}
    shared = True
    try:
        from backend.core.config import settings
        shared = bool(getattr(settings, "agent_kernel_shared_state", True))
    except Exception:
        shared = True
    db_error = None
    if shared:
        try:
            from sqlalchemy import select

            from backend.database import AsyncSessionLocal
            from backend.models.agent_identity import KernelEscalationRecord

            async with AsyncSessionLocal() as session:
                q = select(KernelEscalationRecord)
                if status:
                    q = q.where(KernelEscalationRecord.status == status)
                rows = (await session.execute(q)).scalars().all()
            for r in rows:
                if r.escalation_id in by_id:
                    continue
                by_id[r.escalation_id] = {
                    "id": r.escalation_id,
                    "process_id": r.process_id,
                    "capabilities": r.capabilities or [],
                    "reason": r.reason or "",
                    "status": r.status,
                    "created_at": r.created_at_ts or 0,
                    "resolved_at": r.resolved_at,
                    "resolved_by": r.resolved_by,
                    "source": "db",
                }
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("list_escalations DB merge failed: %s", e)
            db_error = str(e)
    reqs = list(by_id.values())
    if status is not None:
        reqs = [r for r in reqs if r.get("status") == status]
    reqs.sort(key=lambda r: float(r.get("created_at") or 0), reverse=True)
    out = {
        "escalations": reqs,
        "total": len(reqs),
        "shared_state": shared,
    }
    if db_error:
        out["db_warning"] = db_error
    return out


@router.post("/escalations/{request_id}/approve")
async def approve_escalation(
    request_id: str,
    current_user: Annotated[UserRead, Depends(require_admin)],
):
    """审批 Agent 提权：仅管理员（审计 P1：普通用户不得自批 shell）。"""
    kernel = get_kernel()
    # 跨 worker：先把 DB 中的 pending 水合进本进程内存
    await kernel.ensure_escalation_loaded(request_id)
    try:
        req = await kernel.approve_escalation(request_id, by=str(current_user.id))
    except ValueError as e:
        # 必须 4xx：前端 axios 只把非 2xx 当失败，200+error 会假成功
        raise HTTPException(status_code=400, detail=str(e)) from e
    return req.to_dict()


@router.post("/escalations/{request_id}/deny")
async def deny_escalation(
    request_id: str,
    current_user: Annotated[UserRead, Depends(require_admin)],
):
    kernel = get_kernel()
    await kernel.ensure_escalation_loaded(request_id)
    try:
        req = await kernel.deny_escalation(request_id, by=str(current_user.id))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return req.to_dict()


# ── 身份系统（0.5 编制与档案）───────────────────────────────────


def _identity_registry():
    reg = get_kernel().identity_registry
    return reg


def _ident_dict(i) -> dict:
    return {
        "id": str(i.id),
        "name": i.name,
        "role": i.role,
        "status": i.status,
        "capabilities": i.capabilities,
        "credit_score": i.credit_score,
        "default_token_budget": i.default_token_budget,
        "sub_agent_id": str(i.sub_agent_id) if i.sub_agent_id else None,
        "created_at": i.created_at.isoformat() if i.created_at else None,
        "archived_at": i.archived_at.isoformat() if i.archived_at else None,
        "meta": i.meta or {},
    }


def _memory_dict(m) -> dict:
    return {
        "id": str(m.id),
        "identity_id": str(m.identity_id),
        "kind": m.kind,
        "content": m.content,
        "source": m.source,
        "approved_by": m.approved_by,
        "version": m.version,
        "superseded_by": str(m.superseded_by) if m.superseded_by else None,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@router.get("/identities")
async def list_identities(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    status: str | None = Query(None),
):
    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    # 多租户：默认只列当前用户的身份；单用户模式附带 orphan（user_id 为空的历史行）
    from backend.core.config import settings

    include_orphan = bool(getattr(settings, "single_user_mode", True))
    items = await reg.list(
        status=status,
        user_id=current_user.id,
        include_orphan=include_orphan,
    )
    return {"identities": [_ident_dict(i) for i in items], "total": len(items)}


@router.post("/identities")
async def create_identity(
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """创建员工 Identity。

    招聘向导（hire）可传：
    - create_skill_pack=true：自动创建 SubAgent 技能包并挂 sub_agent_id（1:1）
    - persona / duty / initial_memory：写入 Identity Memory（system 源）
    """
    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    role = str(body.get("role") or "").strip()
    caps = body.get("capabilities")
    if caps is not None and not isinstance(caps, list):
        caps = list(caps) if caps else []
    meta = dict(body.get("meta") or {})
    persona = str(body.get("persona") or meta.get("persona") or "").strip()
    duty = str(body.get("duty") or meta.get("duty") or "").strip()
    initial_memory = str(
        body.get("initial_memory") or body.get("init_memory") or ""
    ).strip()
    if persona:
        meta.setdefault("persona", persona)
    if duty:
        meta.setdefault("duty", duty)

    # Hire → Identity：可选自动生成「技能包」SubAgent，关系 1:1 写死在 sub_agent_id
    sub_agent_id = body.get("sub_agent_id")
    create_skill_pack = bool(body.get("create_skill_pack", False))
    if create_skill_pack and not sub_agent_id:
        try:
            from backend.core.config import settings
            from backend.repositories.sub_agent_repo import AsyncSubAgentRepository

            model_ref = (
                str(body.get("model_ref") or "").strip()
                or str(getattr(settings, "default_llm_model", "") or "").strip()
                or str(getattr(settings, "llm_model", "") or "").strip()
                or "default"
            )
            prompt_parts = [
                f"You are {name}" + (f", {role}." if role else "."),
            ]
            if persona:
                prompt_parts.append(f"Persona: {persona}")
            if duty:
                prompt_parts.append(f"Duty: {duty}")
            prompt_parts.append(
                "You are a member of this AI workforce. Be concise, report progress, escalate risks."
            )
            pack_repo = AsyncSubAgentRepository()
            pack = await pack_repo.create(
                {
                    "name": name,
                    "description": role or f"Skill pack for identity {name}",
                    "icon": "👤",
                    "model_ref": model_ref[:128],
                    "system_prompt": "\n".join(prompt_parts),
                    "enabled_toolsets": list(caps or []),
                    "max_iterations": 20,
                    "temperature": 0.3,
                    "enabled": True,
                    "user_id": current_user.id,
                    "is_builtin": False,
                }
            )
            sub_agent_id = pack.id
            meta["skill_pack"] = "sub_agent"
            meta["source"] = meta.get("source") or "hire_wizard"
        except Exception as e:
            logger.warning("hire skill pack create failed (identity still created): %s", e)

    if not meta.get("source") and (create_skill_pack or persona or duty):
        meta["source"] = "hire_wizard"

    try:
        ident = await reg.create(
            name,
            role=role,
            capabilities=caps,
            default_token_budget=body.get("default_token_budget"),
            user_id=current_user.id,
            meta=meta,
            sub_agent_id=sub_agent_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    # 人格/职责/初始记忆落入 Identity Memory（招聘结果可追溯，不只塞 meta）
    for kind, content in (
        ("persona", persona),
        ("duty", duty),
        ("preference", initial_memory),
    ):
        if not content:
            continue
        try:
            await reg.add_memory(
                ident.id, kind, content, source="system", approved_by=str(current_user.id)
            )
        except Exception as e:
            logger.warning("hire identity memory %s skipped: %s", kind, e)

    out = _ident_dict(ident)
    out["skill_pack_linked"] = bool(ident.sub_agent_id)
    return out


@router.post("/identities/{identity_id}/transition")
async def transition_identity(
    identity_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """状态机：suspend / resume / archive（解雇，终态不可逆）。

    action 别名：fire / dismiss / terminate → archive
    """
    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    action = str(body.get("action") or "").strip().lower()
    if action in ("fire", "dismiss", "terminate", "解雇"):
        action = "archive"
    by = str(current_user.id)
    try:
        if action == "suspend":
            ident = await reg.suspend(identity_id, by=by)
        elif action == "resume":
            ident = await reg.resume(identity_id, by=by)
        elif action == "archive":
            ident = await reg.archive(identity_id, by=by)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"unknown action {action!r}（suspend/resume/archive|fire）",
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _ident_dict(ident)


@router.patch("/identities/{identity_id}")
@router.post("/identities/{identity_id}/profile")
async def update_identity_profile(
    identity_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """改名 / 职位(role) / 默认 token 预算。

    body:
      - name?: str
      - role?: str  （职位/职责标题）
      - default_token_budget?: int | null
      - persona?: str  → Identity Memory persona（可选）
      - duty?: str     → Identity Memory duty（可选）
    """
    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")

    kwargs: dict = {"by": str(current_user.id)}
    if "name" in body:
        kwargs["name"] = body.get("name")
    if "role" in body:
        kwargs["role"] = body.get("role")
    if "default_token_budget" in body:
        kwargs["default_token_budget"] = body.get("default_token_budget")

    try:
        ident = await reg.update_profile(identity_id, **kwargs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # 可选：同步人格/职责记忆
    persona = body.get("persona")
    duty = body.get("duty")
    try:
        if isinstance(persona, str) and persona.strip():
            # supersede 现有 persona 或新增
            cur = await reg.current_memory(identity_id, kind="persona")
            if cur:
                await reg.supersede_memory(
                    cur[0].id, persona.strip(), approved_by=str(current_user.id)
                )
            else:
                await reg.add_memory(
                    identity_id,
                    "persona",
                    persona.strip(),
                    source="manual",
                    approved_by=str(current_user.id),
                )
        if isinstance(duty, str) and duty.strip():
            cur = await reg.current_memory(identity_id, kind="duty")
            if cur:
                await reg.supersede_memory(
                    cur[0].id, duty.strip(), approved_by=str(current_user.id)
                )
            else:
                await reg.add_memory(
                    identity_id,
                    "duty",
                    duty.strip(),
                    source="manual",
                    approved_by=str(current_user.id),
                )
    except Exception as e:
        logger.warning("identity memory profile sync: %s", e)

    return _ident_dict(ident)


@router.post("/identities/{identity_id}/capabilities")
async def set_identity_capabilities(
    identity_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """权限档案变更——全程审计（禁止静默改权）。

    body.mode:
      - replace（默认）：整表替换 capabilities
      - grant：合并扩权（CEO 动态赋权）
      - revoke：收回部分能力
    """
    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    mode = str(body.get("mode") or "replace").strip().lower()
    caps_in = body.get("capabilities")
    if not isinstance(caps_in, list) and mode == "replace":
        raise HTTPException(status_code=400, detail="capabilities must be a list")
    try:
        if mode in ("grant", "add", "merge"):
            ident0 = await reg.get(identity_id)
            if ident0 is None:
                raise ValueError(f"未知身份 {identity_id}")
            old = list(ident0.capabilities or [])
            add = [str(c).strip() for c in (caps_in or []) if str(c).strip()]
            # tools[] → cap mapping
            tools = body.get("tools") if isinstance(body.get("tools"), list) else []
            try:
                from backend.agent.grant_store import crew_cap_for_tool

                for t in tools:
                    cap = crew_cap_for_tool(str(t))
                    if cap:
                        add.append(cap)
            except Exception:
                pass
            merged = list(old)
            for c in add:
                if c not in merged:
                    merged.append(c)
            ident = await reg.set_capabilities(
                identity_id, merged, by=f"api_grant:{current_user.id}"
            )
            try:
                from backend.kernel.cap_requests import mark_granted_for_identity

                mark_granted_for_identity(
                    identity_id, caps=add, tools=[str(t) for t in tools], by="api"
                )
            except Exception:
                pass
        elif mode in ("revoke", "remove"):
            ident0 = await reg.get(identity_id)
            if ident0 is None:
                raise ValueError(f"未知身份 {identity_id}")
            drop = {str(c).strip() for c in (caps_in or []) if str(c).strip()}
            new = [c for c in (ident0.capabilities or []) if c not in drop]
            ident = await reg.set_capabilities(
                identity_id, new, by=f"api_revoke:{current_user.id}"
            )
        else:
            ident = await reg.set_capabilities(
                identity_id, caps_in, by=str(current_user.id)
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _ident_dict(ident)


@router.get("/cap-requests")
async def list_cap_requests(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    identity_id: str | None = Query(None),
    limit: int = Query(40, ge=1, le=100),
):
    """待 CEO 处理的员工扩权请求（工具被 steward 拒时自动登记）。"""
    from backend.kernel.cap_requests import list_pending

    items = list_pending(identity_id=identity_id, limit=limit)
    return {"items": items, "total": len(items)}


@router.get("/identities/{identity_id}/memory")
async def get_identity_memory(
    identity_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    kind: str | None = Query(None),
):
    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    try:
        items = await reg.current_memory(identity_id, kind=kind)
    except ValueError as e:
        # 非法 UUID 等 → 404，避免前端当成「记忆接口挂了」
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"memory": [_memory_dict(m) for m in items], "total": len(items)}


@router.get("/identities/{identity_id}/growth")
async def get_identity_growth(
    identity_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """Phase 4.2：身份成长档案聚合（记忆版本链 + 技能评分 + Run 统计）。"""
    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    try:
        ident = await reg.get(identity_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if ident is None:
        raise HTTPException(status_code=404, detail="identity not found")

    # 记忆：当前生效 + 尽量拉历史（含已 superseded）做时间线
    timeline: list[dict] = []
    try:
        current = await reg.current_memory(identity_id)
        for m in current:
            d = _memory_dict(m)
            d["is_current"] = True
            timeline.append(d)
        # 若 registry 暴露 list 全量则补充链
        list_all = getattr(reg, "list_memory_history", None) or getattr(
            reg, "list_all_memory", None
        )
        if callable(list_all):
            hist = await list_all(identity_id)
            seen = {t["id"] for t in timeline}
            for m in hist or []:
                d = _memory_dict(m)
                if d.get("id") in seen:
                    continue
                d["is_current"] = not bool(getattr(m, "superseded_by", None))
                timeline.append(d)
    except Exception:
        pass
    timeline.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)

    # 技能评分曲线（evolution scoreboard）
    skills_out: list[dict] = []
    try:
        from backend.evolution import store as evo_store

        assets = evo_store.list_assets(kind="skill", limit=100)
        names = sorted({a.get("name") for a in assets if a.get("name")})
        for name in names[:40]:
            latest = evo_store.latest_asset_by_name("skill", name)
            if not latest:
                continue
            gen = int(latest.get("gen") or 0)
            series = []
            for g in range(max(0, gen - 4), gen + 1):
                st = evo_store.skill_outcome_stats(name, g, window=50)
                series.append({"gen": g, **st})
            skills_out.append(
                {
                    "name": name,
                    "gen": gen,
                    "status": latest.get("status"),
                    "last_score": latest.get("last_score"),
                    "series": series,
                    "current": evo_store.skill_outcome_stats(name, gen, window=50),
                }
            )
    except Exception:
        pass

    # Run 统计：按 identity_id 列过滤
    runs_summary = {
        "total": 0,
        "done": 0,
        "failed": 0,
        "avg_iterations": 0.0,
        "token_used": 0,
    }
    try:
        import uuid as _uuid

        from backend.repositories.agent_run_repo import AsyncAgentRunRepository

        rid = _uuid.UUID(str(identity_id))
        rows = await AsyncAgentRunRepository().list_recent(
            limit=100, identity_id=rid
        )
        runs_summary["total"] = len(rows)
        iters = []
        tokens = 0
        for r in rows:
            st = str(getattr(r, "status", "") or "")
            if st in ("done", "completed"):
                runs_summary["done"] += 1
            elif st in ("failed", "error"):
                runs_summary["failed"] += 1
            try:
                iters.append(int(getattr(r, "total_iterations", 0) or 0))
            except (TypeError, ValueError):
                pass
            try:
                tokens += int(getattr(r, "token_used", 0) or 0)
            except (TypeError, ValueError):
                pass
        if iters:
            runs_summary["avg_iterations"] = round(sum(iters) / len(iters), 2)
        runs_summary["token_used"] = tokens
    except Exception:
        pass

    return {
        "identity": _ident_dict(ident),
        "memory_timeline": timeline,
        "skills": skills_out,
        "runs": runs_summary,
    }


@router.post("/identities/{identity_id}/memory")
async def add_identity_memory(
    identity_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    # 招聘向导等前端自定义 source 归一到合法枚举
    raw_source = str(body.get("source") or "manual").strip() or "manual"
    source_alias = {
        "hire-wizard": "system",
        "hire": "system",
        "wizard": "system",
        "onboarding": "system",
    }
    source = source_alias.get(raw_source, raw_source)
    try:
        entry = await reg.add_memory(
            identity_id,
            str(body.get("kind") or ""),
            str(body.get("content") or ""),
            source=source,
            approved_by=body.get("approved_by") or (
                str(current_user.id) if source == "manual" else None
            ),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _memory_dict(entry)


@router.post("/identities/{identity_id}/memory/{entry_id}/supersede")
async def supersede_identity_memory(
    identity_id: str,
    entry_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    try:
        entry = await reg.supersede_memory(
            entry_id,
            str(body.get("content") or ""),
            approved_by=str(body.get("approved_by") or current_user.id),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _memory_dict(entry)


@router.post("/identities/{identity_id}/memory/{entry_id}/retire")
async def retire_identity_memory(
    identity_id: str,
    entry_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    body: dict | None = None,
):
    """废止记忆：tombstone supersede，Assembler 不再注入。"""
    from backend.kernel.crew_memory import get_crew_memory_writer

    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    payload = body or {}
    try:
        writer = get_crew_memory_writer(reg)
        entry = await writer.retire(
            entry_id,
            approved_by=str(payload.get("approved_by") or current_user.id),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _memory_dict(entry)


@router.post("/identities/{identity_id}/memory/preview")
async def preview_identity_memory_inject(
    identity_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """预览将注入到 prompt 的记忆块（派活前可解释）。"""
    from backend.kernel.crew_memory import get_crew_memory_assembler

    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    instruction = str(body.get("instruction") or "")
    mode = str(body.get("mode") or "preview")
    if mode not in ("workforce", "chat", "preview", "compact"):
        mode = "preview"
    try:
        asm = get_crew_memory_assembler(reg)
        result = await asm.build_inject_block(
            identity_id, instruction, mode=mode  # type: ignore[arg-type]
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {
        "header": result.header,
        "body": result.body,
        "text": f"{result.header}\n{result.body}",
        "entries_used": [
            {
                "id": e.id,
                "kind": e.kind,
                "version": e.version,
                "chars": e.chars,
            }
            for e in result.entries_used
        ],
        "truncated": result.truncated,
        "token_estimate": result.token_estimate,
        "mode": result.mode,
    }


@router.post("/identities/{identity_id}/memory/distill-from-item")
async def distill_memory_from_item(
    identity_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """从已完成工单手动沉淀 experience（force，走审批人=当前用户）。"""
    from backend.kernel.crew_memory import get_crew_memory_writer
    from backend.kernel.workforce import get_workforce_inbox

    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="identity layer disabled")
    item_id = str(body.get("inbox_item_id") or body.get("item_id") or "").strip()
    if not item_id:
        raise HTTPException(status_code=400, detail="inbox_item_id required")

    inbox = get_workforce_inbox()
    if inbox is None:
        raise HTTPException(status_code=503, detail="inbox disabled")

    items = await inbox.list_items(identity_id=identity_id, limit=200)
    item = next((i for i in items if str(i.id) == item_id), None)
    if item is None:
        # 放宽：不限 identity 再找一次
        items2 = await inbox.list_items(limit=300)
        item = next((i for i in items2 if str(i.id) == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="inbox item not found")
    if str(item.identity_id) != str(identity_id):
        raise HTTPException(status_code=400, detail="item does not belong to identity")

    status = str(getattr(item, "status", "") or "")
    if status not in ("done", "completed"):
        raise HTTPException(
            status_code=400,
            detail=f"only done jobs can distill (status={status})",
        )

    writer = get_crew_memory_writer(reg)
    entry = await writer.maybe_distill_from_job(
        identity_id=identity_id,
        instruction=str(getattr(item, "instruction", "") or ""),
        result=str(getattr(item, "result", "") or ""),
        process_id=str(getattr(item, "process_id", "") or "") or None,
        status="done",
        force=True,
        approved_by=str(current_user.id),
        source="manual",
    )
    if entry is None:
        raise HTTPException(
            status_code=400,
            detail="distill skipped (failure markers or empty result)",
        )
    return _memory_dict(entry)


# ── LLM 调度观测 ─────────────────────────────────────────────


@router.get("/scheduler/status")
async def scheduler_status(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """LLM 公平调度：在飞 / 排队 / 配额（P0-C 优先 Rust host）。"""
    from backend.kernel.llm_scheduler import get_llm_admission

    st = get_llm_admission().status()
    # 附带 run 队列 stats
    try:
        from backend.kernel import get_kernel

        k = get_kernel()
        if hasattr(k, "scheduler") and hasattr(k.scheduler, "stats"):
            st["run_queue"] = k.scheduler.stats()
        elif hasattr(k, "_call"):
            st["run_queue"] = await k._acall("scheduler_stats") or {}
    except Exception:
        pass
    return st


@router.post("/scheduler/reclaim")
async def scheduler_reclaim(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    force: bool = Query(False, description="清空全部 LLM 槽位（紧急自救）"),
    null_pid_max_hold_secs: float = Query(
        120.0, ge=0, le=3600, description="无 process_id 的租约最长持有秒"
    ),
    max_hold_secs: float = Query(
        600.0, ge=30, le=7200, description="任意租约最长持有秒"
    ),
):
    """回收孤儿/过期 LLM 租约，解除 per_identity / max_in_flight 死锁。

    force=true 时清空全部 in_flight 与排队（会打断进行中的模型调用）。
    """
    from backend.kernel.llm_scheduler import get_llm_admission

    result = await get_llm_admission().reclaim(
        null_pid_max_hold_secs=null_pid_max_hold_secs,
        max_hold_secs=max_hold_secs,
        force=force,
    )
    return result


@router.get("/resources/{process_id}")
async def process_resources(
    process_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """P0-C：进程资源账户快照。"""
    from backend.kernel import get_kernel

    k = get_kernel()
    if hasattr(k, "resource_usage"):
        usage = k.resource_usage(process_id)
    elif hasattr(k, "_call"):
        usage = await k._acall("resource_usage", {"process_id": process_id}) or {}
    else:
        usage = {}
    proc = k.get_process(process_id)
    return {
        "process_id": process_id,
        "state": getattr(proc, "state", None) if proc else None,
        "resources": usage,
    }


@router.get("/run_queue")
async def run_queue_status(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """P0-C：Agent run 优先级队列。"""
    from backend.kernel import get_kernel

    k = get_kernel()
    if hasattr(k, "_call"):
        return {
            "stats": await k._acall("scheduler_stats") or {},
            "backend": "rust",
        }
    if hasattr(k, "scheduler"):
        return {"stats": k.scheduler.stats(), "backend": "python"}
    return {"stats": {}, "backend": "none"}


@router.get("/decision_trail/{process_id}")
async def decision_trail(
    process_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    limit: int = Query(500, ge=1, le=5000),
):
    """P0-D：导出进程决策轨迹（mediation / policy.decision / checkpoint）。"""
    from backend.kernel import get_kernel

    k = get_kernel()
    if hasattr(k, "_call"):
        return await k._acall(
            "export_decision_trail",
            {"process_id": process_id, "limit": limit},
        ) or {"process_id": process_id, "events": [], "total": 0}
    # python fallback
    events = k.events(process_id=process_id, limit=limit)
    trail = [
        e.to_dict() if hasattr(e, "to_dict") else e
        for e in events
        if getattr(e, "kind", "")
        in ("mediation", "policy.decision", "checkpoint.begin", "checkpoint.restore")
    ]
    return {"process_id": process_id, "events": trail, "total": len(trail)}


@router.get("/runs/{process_id}/replay")
async def run_replay(
    process_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    limit: int = Query(1000, ge=1, le=10000),
):
    """H-10：一次 run 的可还原时间线（工具 + 裁决理由 + 预算/资源快照）。

    不是全量 process 状态机 replay（禁止）；而是决策与扣费事件的有序重建。
    """
    k = get_kernel()
    process_id = str(process_id or "").strip()
    if not process_id:
        raise HTTPException(status_code=400, detail="process_id required")

    trail: dict[str, Any] = {"process_id": process_id, "events": [], "total": 0}
    if hasattr(k, "export_decision_trail"):
        try:
            trail = k.export_decision_trail(process_id, limit=limit) or trail
        except TypeError:
            trail = k.export_decision_trail(process_id) or trail
        except Exception as e:
            logger.debug("export_decision_trail: %s", e)
    elif hasattr(k, "_call"):
        trail = (
            await k._acall(
                "export_decision_trail",
                {"process_id": process_id, "limit": limit},
            )
            or trail
        )
    else:
        events = k.events(process_id=process_id, limit=limit)
        trail = {
            "process_id": process_id,
            "events": [
                e.to_dict() if hasattr(e, "to_dict") else e for e in events
            ],
            "total": 0,
        }
        trail["total"] = len(trail["events"])

    raw_events = list(trail.get("events") or [])
    tools: list[dict[str, Any]] = []
    court: list[dict[str, Any]] = []
    budget_snaps: list[dict[str, Any]] = []
    timeline: list[dict[str, Any]] = []

    for ev in raw_events:
        if not isinstance(ev, dict):
            if hasattr(ev, "to_dict"):
                ev = ev.to_dict()
            else:
                continue
        kind = str(ev.get("kind") or ev.get("type") or "")
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else ev
        entry = {
            "kind": kind,
            "ts": ev.get("ts") or ev.get("timestamp") or payload.get("ts"),
            "payload": payload,
        }
        timeline.append(entry)
        low = kind.lower()
        if "mediat" in low or low in ("tool_call", "tool.allow", "tool.deny"):
            tools.append(entry)
        if "policy" in low or "court" in low or "decision" in low:
            court.append(entry)
        if any(
            x in low
            for x in (
                "budget",
                "charge",
                "resource",
                "token",
                "soft_renew",
            )
        ):
            budget_snaps.append(entry)

    proc_snapshot: dict[str, Any] = {}
    try:
        p = k.get_process(process_id) if hasattr(k, "get_process") else None
        if p is not None:
            proc_snapshot = p.to_dict() if hasattr(p, "to_dict") else {
                "id": getattr(p, "id", process_id),
                "tokens_used": getattr(p, "tokens_used", None),
                "token_budget": getattr(p, "token_budget", None),
                "state": getattr(p, "state", None),
                "capabilities": list(getattr(p, "capabilities", None) or [])
                if getattr(p, "capabilities", None) is not None
                else None,
            }
    except Exception:
        pass

    resources: dict[str, Any] = {}
    if hasattr(k, "resource_usage"):
        try:
            resources = k.resource_usage(process_id) or {}
        except Exception:
            resources = {}
    elif hasattr(k, "_call"):
        try:
            resources = await k._acall("resource_usage", {"process_id": process_id}) or {}
        except Exception:
            resources = {}

    return {
        "process_id": process_id,
        "full_state_replay_forbidden": True,
        "note": (
            "H-10 decision/cost timeline only — not a full AgentProcess state machine "
            "replay (see recovery plan)."
        ),
        "process": proc_snapshot,
        "resources": resources,
        "tools": tools,
        "court": court,
        "budget_resource_events": budget_snaps,
        "timeline": timeline,
        "total_events": len(timeline),
    }


# ── P0.5：策略 / 成本 / 马拉松 / 恢复说明 ─────────────────────


@router.get("/policy/{process_id}")
async def process_policy(
    process_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """P0.5：iteration / doom 策略状态 + 恢复入口。"""
    from backend.agent.exit_reasons import describe_exit_reason
    from backend.kernel import get_kernel

    k = get_kernel()
    out: dict[str, Any] = {"process_id": process_id}
    if hasattr(k, "_call"):
        out["iteration"] = await k._acall("iteration_status", {"process_id": process_id}) or {}
        out["doom"] = await k._acall("doom_status", {"process_id": process_id}) or {}
        out["policy"] = await k._acall("policy_status") or {}
        out["recovery"] = await k._acall(
            "process_recovery_plan", {"process_id": process_id}
        ) or {}
    else:
        out["iteration"] = {}
        out["doom"] = {}
        out["policy"] = {}
        out["recovery"] = {}
    out["resume"] = {
        "method": "POST",
        "path": f"/api/kernel/processes/{process_id}/resume",
        "hint": describe_exit_reason("kernel_gate_stop")["recovery_hint"],
    }
    if out.get("doom", {}).get("tripped"):
        out["exit"] = describe_exit_reason("doom_loop")
    elif out.get("iteration", {}).get("exhausted"):
        out["exit"] = describe_exit_reason("kernel_iteration_exhausted")
    return out


@router.post("/multi-agent/demo")
async def multi_agent_demo(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """M-01：Rust 权威双 Agent ping-pong 演示路径（产品化默认协作样例）。"""
    from backend.kernel import get_kernel

    k = get_kernel()
    if hasattr(k, "multi_agent_demo"):
        return k.multi_agent_demo()
    if hasattr(k, "_call"):
        return await k._acall("multi_agent_demo") or {"ok": False, "error": "rpc empty"}
    raise HTTPException(status_code=501, detail="multi_agent_demo requires Rust kernel host")


@router.get("/eval/status")
async def eval_status_api(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """M-07：内核内 Eval 账本状态。"""
    from backend.kernel import get_kernel

    k = get_kernel()
    if hasattr(k, "eval_status"):
        return k.eval_status()
    if hasattr(k, "_call"):
        return await k._acall("eval_status") or {}
    return {"runs": 0}


@router.post("/eval/record")
async def eval_record_api(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    body: dict[str, Any] | None = None,
):
    """M-07：写入一次 Eval 分数（Rust ledger）。"""
    from backend.kernel import get_kernel

    data = body or {}
    k = get_kernel()
    suite = str(data.get("suite") or "default")
    overall = float(data.get("overall") or 0)
    parts = data.get("parts") if isinstance(data.get("parts"), dict) else {}
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    if hasattr(k, "eval_record"):
        return k.eval_record(suite, overall, parts, meta)
    if hasattr(k, "_call"):
        return await k._acall(
            "eval_record",
            {"suite": suite, "overall": overall, "parts": parts, "meta": meta},
        ) or {}
    raise HTTPException(status_code=501, detail="eval_record requires Rust kernel")


@router.get("/eval/gate")
async def eval_gate_api(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    suite: str | None = Query(None),
):
    """M-07：Eval 门禁检查（周报/CI 可消费）。"""
    from backend.kernel import get_kernel

    k = get_kernel()
    if hasattr(k, "eval_gate_check"):
        return k.eval_gate_check(suite)
    if hasattr(k, "_call"):
        params: dict[str, Any] = {}
        if suite:
            params["suite"] = suite
        return await k._acall("eval_gate_check", params) or {}
    return {"ok": False, "reason": "no_kernel"}


@router.post("/agent-manifest/validate")
async def agent_manifest_validate_api(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    body: dict[str, Any] | None = None,
):
    """M-08：Rust 校验 agent.json（SDK pack）。"""
    from backend.kernel import get_kernel

    data = body or {}
    k = get_kernel()
    if hasattr(k, "agent_manifest_validate"):
        if isinstance(data.get("json"), str):
            return k.agent_manifest_validate(json_str=data["json"])
        return k.agent_manifest_validate(data.get("manifest") or data)
    if hasattr(k, "_call"):
        if isinstance(data.get("json"), str):
            return await k._acall("agent_manifest_validate", {"json": data["json"]}) or {}
        return await k._acall(
            "agent_manifest_validate",
            {"manifest": data.get("manifest") or data},
        ) or {}
    raise HTTPException(status_code=501, detail="agent_manifest_validate requires Rust kernel")


@router.get("/cost")
async def cost_panel(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    process_id: str | None = Query(None),
):
    """P0.5 R5：三维成本面板 — token / billable / resource + cache_hit_rate。

    Aggregates prefer the durable usage_ledger (JSON under TEVARN data dir)
    so totals survive kernel-host restarts. Host in-memory ledger is still
    charged best-effort for live process views.
    """
    from backend.kernel import get_kernel
    from backend.services.usage_ledger import (
        merge_cache_panels,
        merge_cost_panels,
        snapshot_cache,
        snapshot_cost,
    )

    k = get_kernel()
    panel: dict[str, Any] = {
        "tokens_billable": {},
        "cache": {},
        "resources": {},
        "marathon": {},
        "backend": "none",
    }
    host_cost: dict[str, Any] = {}
    host_cache: dict[str, Any] = {}
    if hasattr(k, "_call"):
        panel["backend"] = "rust"
        try:
            host_cost = await k._acall("cost_panel") or {}
        except Exception:
            host_cost = {}
        try:
            host_cache = await k._acall("cache_metrics") or {}
        except Exception:
            host_cache = {}
        try:
            panel["marathon"] = await k._acall("marathon_metrics") or {}
        except Exception:
            panel["marathon"] = {}
        if process_id:
            try:
                panel["process_cost"] = (
                    await k._acall("cost_process", {"process_id": process_id}) or {}
                )
            except Exception:
                panel["process_cost"] = {}
            try:
                panel["process_resources"] = (
                    await k._acall("resource_usage", {"process_id": process_id}) or {}
                )
            except Exception:
                panel["process_resources"] = {}
        # live process token rollup + resource aggregate (R-05)
        try:
            procs = k.list_processes(include_terminal=False) or []
            token_used = 0
            token_budget = 0
            res_agg: dict[str, dict[str, int]] = {}
            for p in procs:
                token_used += int(getattr(p, "tokens_used", 0) or 0)
                b = getattr(p, "token_budget", None)
                if b is not None:
                    token_budget += int(b or 0)
                pid = str(getattr(p, "id", "") or "")
                if pid and hasattr(k, "resource_usage"):
                    try:
                        u = k.resource_usage(pid) or {}
                        if isinstance(u, dict):
                            for kind, acct in u.items():
                                if not isinstance(acct, dict):
                                    continue
                                bucket = res_agg.setdefault(
                                    str(kind), {"used": 0, "limit": 0}
                                )
                                bucket["used"] += int(acct.get("used") or 0)
                                lim = acct.get("limit")
                                if lim is not None:
                                    bucket["limit"] = max(
                                        int(bucket["limit"] or 0), int(lim)
                                    )
                    except Exception:
                        pass
            panel["live_processes"] = {
                "count": len(procs),
                "tokens_used": token_used,
                "token_budget_sum": token_budget,
            }
            if res_agg and not process_id:
                panel["resources"] = res_agg
        except Exception:
            panel["live_processes"] = {}
    elif hasattr(k, "cost_panel"):
        panel["backend"] = "python"
        try:
            host_cost = k.cost_panel() or {}
        except Exception:
            host_cost = {}
        try:
            if hasattr(k, "cache_metrics"):
                host_cache = k.cache_metrics() or {}
        except Exception:
            pass
    # Merge durable ledger (authoritative after host restarts)
    try:
        durable_cost = snapshot_cost()
    except Exception:
        durable_cost = {}
    try:
        durable_cache = snapshot_cache()
    except Exception:
        durable_cache = {}
    panel["tokens_billable"] = merge_cost_panels(host_cost, durable_cost)
    panel["cache"] = merge_cache_panels(host_cache, durable_cache)
    panel["ledger_source"] = panel["tokens_billable"].get("source") or "empty"
    # single-process resource detail
    if process_id and hasattr(k, "resource_usage"):
        try:
            panel["resources"] = k.resource_usage(process_id) or panel.get("resources") or {}
        except Exception:
            pass
    # R-05：统一摘要字段，前端/编制共用
    tb = panel.get("tokens_billable") or {}
    totals = tb.get("totals") if isinstance(tb, dict) else {}
    if not isinstance(totals, dict):
        totals = {}
    cache = panel.get("cache") or {}
    ctot = cache.get("totals") if isinstance(cache, dict) else {}
    if not isinstance(ctot, dict):
        ctot = {}
    live = panel.get("live_processes") or {}
    # 提升 by_family / by_model / 日序到顶层，便于用量页筛选与热力图
    if isinstance(tb, dict):
        panel["by_family"] = tb.get("by_family") or {}
        panel["by_model"] = tb.get("by_model") or {}
        panel["by_day"] = tb.get("by_day") or {}
        panel["by_model_day"] = tb.get("by_model_day") or {}
        panel["totals"] = totals
    if isinstance(cache, dict):
        panel["cache_families"] = cache.get("families") or {}
        panel["cache_models"] = cache.get("models") or {}
    # Prefer token-level cache hit rate (cache_read / prompt) for compression work
    # Prefer cost-ledger prompt/cache_read over cache panel when both exist (same charge path)
    token_hit = totals.get("token_cache_hit_rate")
    if token_hit is None:
        token_hit = ctot.get("token_hit_rate")
    if token_hit is None:
        pt = int(totals.get("prompt") or 0)
        cr = int(totals.get("cache_read") or 0)
        if pt <= 0:
            pt = int(ctot.get("prompt_tokens") or 0)
            cr = int(ctot.get("cache_read_tokens") or 0)
        token_hit = (cr / pt) if pt > 0 else None
    # Integrity: sum(by_model.tokens) should match totals when models present;
    # multi-provider families must also sum to totals; daily maps must not exceed.
    model_tok_sum = 0
    model_bill_sum = 0
    for _mk, _mb in (panel.get("by_model") or {}).items():
        if isinstance(_mb, dict):
            model_tok_sum += int(_mb.get("tokens") or 0)
            model_bill_sum += int(_mb.get("billable") or 0)
    family_tok_sum = 0
    for _fk, _fb in (panel.get("by_family") or {}).items():
        if isinstance(_fb, dict):
            family_tok_sum += int(_fb.get("tokens") or 0)
    day_tok_sum = 0
    for _db in (panel.get("by_day") or {}).values():
        if isinstance(_db, dict):
            day_tok_sum += int(_db.get("tokens") or 0)
    model_day_tok_sum = 0
    for _days in (panel.get("by_model_day") or {}).values():
        if not isinstance(_days, dict):
            continue
        for _db in _days.values():
            if isinstance(_db, dict):
                model_day_tok_sum += int(_db.get("tokens") or 0)
    tot_tok = int(totals.get("tokens") or 0)
    tot_bill = int(totals.get("billable") or 0)
    # by_day may be partial (pre-upgrade lifetime only) — never require day==totals
    attribution_ok = True
    if model_tok_sum > 0 and tot_tok > 0 and abs(model_tok_sum - tot_tok) > 1:
        attribution_ok = False
    if family_tok_sum > 0 and tot_tok > 0 and abs(family_tok_sum - tot_tok) > 1:
        attribution_ok = False
    if day_tok_sum > 0 and model_day_tok_sum > 0 and abs(day_tok_sum - model_day_tok_sum) > 1:
        attribution_ok = False
    if day_tok_sum > 0 and tot_tok > 0 and day_tok_sum > tot_tok + 1:
        attribution_ok = False
    panel["summary"] = {
        "tokens": tot_tok if tot_tok > 0 else int(live.get("tokens_used") or 0),
        "billable": tot_bill,
        "prompt": int(totals.get("prompt") or 0),
        "completion": int(totals.get("completion") or 0),
        "cache_read": int(totals.get("cache_read") or 0),
        "cache_write": int(totals.get("cache_write") or 0),
        "real_rounds": int(totals.get("real_rounds") or 0),
        "estimated_rounds": int(totals.get("estimated_rounds") or 0),
        "cache_hit_rate": token_hit if token_hit is not None else ctot.get("hit_rate"),
        "round_cache_hit_rate": ctot.get("hit_rate"),
        "token_cache_hit_rate": token_hit,
        "live_process_count": int(live.get("count") or 0),
        "resource_kinds": list((panel.get("resources") or {}).keys())
        if isinstance(panel.get("resources"), dict)
        else [],
        "ledger_source": panel.get("ledger_source"),
        "model_tokens_sum": model_tok_sum,
        "model_billable_sum": model_bill_sum,
        "family_tokens_sum": family_tok_sum,
        "day_tokens_sum": day_tok_sum,
        "model_day_tokens_sum": model_day_tok_sum,
        "has_by_model_day": bool(panel.get("by_model_day")),
        "attribution_ok": attribution_ok,
    }
    return panel


@router.get("/cache/metrics")
async def cache_metrics_api(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """P0.5 / R-04：provider family 级 cache_hit_rate（durable ∪ host）。"""
    from backend.kernel import get_kernel
    from backend.services.usage_ledger import merge_cache_panels, snapshot_cache

    host: dict[str, Any] = {}
    try:
        k = get_kernel()
        if hasattr(k, "cache_metrics"):
            host = k.cache_metrics() or {}
        elif hasattr(k, "_call"):
            host = await k._acall("cache_metrics") or {}
    except Exception:
        host = {}
    try:
        durable = snapshot_cache()
    except Exception:
        durable = {}
    return merge_cache_panels(host, durable)


@router.get("/results/{handle_id}")
async def result_load_api(
    handle_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    process_id: str = Query(..., description="spill 所属 kernel process_id（绑定校验）"),
    preview_only: bool = Query(False),
):
    """R-01：按 spill 句柄取回完整工具结果（或预览）。

    必须传 process_id，与写入 spill 时的进程一致，防止横向读其它任务结果。
    """
    from backend.kernel import get_kernel

    k = get_kernel()
    hid = str(handle_id or "").strip()
    pid = str(process_id or "").strip()
    if not hid:
        raise HTTPException(status_code=400, detail="handle_id required")
    if not pid:
        raise HTTPException(status_code=400, detail="process_id required")
    data: dict[str, Any] = {}
    if hasattr(k, "result_load"):
        try:
            data = k.result_load(hid, process_id=pid) or {}
        except TypeError:
            data = await k._acall(
                "result_load", {"handle_id": hid, "process_id": pid}
            ) or {}
    elif hasattr(k, "_call"):
        data = await k._acall(
            "result_load", {"handle_id": hid, "process_id": pid}
        ) or {}
    else:
        raise HTTPException(status_code=501, detail="result_load requires Rust kernel host")
    if not data or data.get("error"):
        err = str(data.get("error") or data.get("message") or "handle not found")
        code = 403 if "process" in err.lower() else 404
        raise HTTPException(status_code=code, detail=err)
    if preview_only and "content" in data:
        content = str(data.get("content") or "")
        data = {
            **{kk: vv for kk, vv in data.items() if kk != "content"},
            "preview": content[:800],
            "bytes": len(content.encode("utf-8", errors="replace")),
        }
    return data


@router.get("/marathon/metrics")
async def marathon_metrics_api(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """P0.5：marathon_resume_success 等长程指标。"""
    from backend.kernel import get_kernel

    k = get_kernel()
    if hasattr(k, "marathon_metrics"):
        return k.marathon_metrics()
    if hasattr(k, "_call"):
        return await k._acall("marathon_metrics") or {}
    return {}


@router.get("/weekly")
async def weekly_report_api(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    week: str | None = Query(None, description="ISO week e.g. 2026-W31；默认 latest"),
    refresh: bool = Query(False, description="重新采集快照（不重跑 eval）"),
):
    """债 #2：观测 / Eval 周报（cost · cache · marathon · eval · pkg · wasm）。"""
    from backend.services.weekly_report import (
        collect_weekly_report,
        load_weekly_report,
    )

    if week and not refresh:
        rep = load_weekly_report(week)
        if rep:
            return rep
        raise HTTPException(status_code=404, detail=f"no weekly report for {week}")
    if not refresh:
        rep = load_weekly_report(None)
        if rep:
            return rep
    from backend.kernel import get_kernel

    return collect_weekly_report(get_kernel(), run_eval=False, persist=True)


@router.get("/dashboard")
async def kernel_dashboard(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """T6：观测聚合 — 资源/调度/成本/沙箱/周报指针 一页。"""
    from backend.core.config import settings
    from backend.kernel import get_kernel

    k = get_kernel()

    async def call(method: str, params: dict | None = None) -> Any:
        try:
            # audit-fix: 走 _acall，避免阻塞事件循环
            if hasattr(k, "_acall"):
                return await k._acall(method, params or {}) or {}
            fn = getattr(k, method, None)
            return fn() if callable(fn) and not params else {}
        except Exception as e:
            return {"error": str(e)}

    sandbox = {}
    try:
        from backend.agent.working_mode import decide_sandbox, resolve_execution_mode
        from backend.computer.detect import detect_sandbox_capability

        cap = detect_sandbox_capability()
        dec = decide_sandbox()
        sandbox = {
            "execution_mode": resolve_execution_mode(),
            "use_sandbox": dec.use_sandbox,
            "degraded": dec.degraded,
            "capability": {
                "mode": cap.mode,
                "level": cap.level,
                "label": cap.label,
            },
            "reason": dec.reason,
            "coverage_hint": (
                "full"
                if dec.use_sandbox and cap.level == "full"
                else ("restricted" if dec.use_sandbox else "none")
            ),
        }
    except Exception as e:
        sandbox = {"error": str(e)}

    weekly_ptr = {}
    try:
        from backend.services.weekly_report import load_weekly_report

        w = load_weekly_report(None)
        if w:
            weekly_ptr = {
                "week": w.get("week"),
                "health": (w.get("health") or {}).get("overall"),
                "eval": (w.get("eval") or {}).get("overall")
                if isinstance(w.get("eval"), dict)
                else None,
            }
    except Exception:
        pass

    return {
        "backend": getattr(settings, "agent_kernel_backend", "rust"),
        "run_gate_required": bool(
            getattr(settings, "agent_kernel_run_gate_required", True)
        ),
        "court_rust_required": bool(
            getattr(settings, "agent_court_rust_required", True)
        ),
        "run_gate": await call("run_gate_status"),
        "scheduler": await call("scheduler_stats"),
        "cost": await call("cost_panel"),
        "cache": await call("cache_metrics"),
        "marathon": await call("marathon_metrics"),
        "pkg": await call("pkg_status"),
        "wasm": await call("wasm_status"),
        "sandbox": sandbox,
        "weekly": weekly_ptr,
        "live_processes": len(k.list_processes(include_terminal=False) or [])
        if hasattr(k, "list_processes")
        else 0,
        "governance": {
            "soft_renew_enabled": bool(
                getattr(settings, "agent_budget_soft_renew_enabled", False)
            )
            and not bool(getattr(settings, "agent_budget_hard_cap_only", False)),
            "hard_cap_only": bool(getattr(settings, "agent_budget_hard_cap_only", False)),
            "require_intent": bool(
                getattr(settings, "agent_kernel_require_intent", True)
            ),
            "production_guard": True,
        },
    }


@router.post("/resources/{process_id}/sample-rss")
async def sample_process_rss(
    process_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    os_pid: int | None = Query(None, description="可选 OS pid；默认采样本后端进程"),
    session_id: str | None = Query(None, description="会话绑定（交互进程建议必填）"),
):
    """资源加深：采样 RSS（交互进程须 session 匹配）。"""
    from backend.kernel import get_kernel
    from backend.kernel.process_access import assert_process_accessible
    from backend.kernel.resource_os import sample_and_report
    from backend.kernel.resource_os import status as ros_status

    k = get_kernel()
    try:
        # 与 collab 一致：强制 session 绑定（wf: 编制进程可无 session 仅靠 identity）
        assert_process_accessible(
            k,
            process_id,
            session_id=session_id,
            require_session=True,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=404 if "not found" in str(e).lower() else 403,
            detail=str(e),
        ) from e
    r = sample_and_report(process_id, os_pid=os_pid)
    r["os"] = ros_status()
    if r.get("over_limit"):
        r["action"] = "flag_over_limit"
    return r


@router.get("/resources/os-status")
async def resource_os_status(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    from backend.kernel.resource_os import status as ros_status

    return ros_status()


@router.get("/sandbox/coverage")
async def sandbox_coverage(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """T4：当前执行环境与平台沙箱能力覆盖率快照。"""
    from backend.agent.working_mode import decide_sandbox, resolve_execution_mode
    from backend.computer.detect import detect_sandbox_capability
    from backend.core.config import settings

    cap = detect_sandbox_capability()
    dec = decide_sandbox()
    mode = resolve_execution_mode()
    # 简易覆盖率：sandbox 强制且有能力 → 1.0；auto 有能力 → 0.8；local → 0
    if mode == "local":
        score = 0.0
    elif mode == "sandbox" and cap.available:
        score = 1.0 if cap.level == "full" else 0.7
    elif mode == "auto" and cap.available:
        score = 0.8 if cap.level == "full" else 0.5
    elif mode == "sandbox" and not cap.available:
        score = 0.0  # fail-closed 将拒绝执行
    else:
        score = 0.0
    return {
        "score": score,
        "execution_mode": mode,
        "default_execution_mode": str(
            getattr(settings, "agent_execution_mode", "") or "sandbox"
        ),
        "use_sandbox": dec.use_sandbox,
        "degraded": dec.degraded,
        "capability": {
            "mode": cap.mode,
            "level": cap.level,
            "available": cap.available,
            "label": cap.label,
            "note": cap.note,
        },
        "metric": "sandbox_default_coverage",
    }


class CollabPlanBody(BaseModel):
    process_id: str
    steps: list[str] = Field(default_factory=list)
    session_id: str | None = None


class CollabInterruptBody(BaseModel):
    process_id: str
    reason: str = ""
    session_id: str | None = None


class CollabApprovalBody(BaseModel):
    process_id: str
    request_id: str | None = None
    approve: bool = True
    note: str = ""
    session_id: str | None = None


def _require_process(
    k: Any,
    process_id: str,
    *,
    session_id: str | None = None,
    require_session: bool = True,
) -> dict[str, Any]:
    """默认强制 session 绑定（交互进程）。"""
    from backend.kernel.process_access import assert_process_accessible

    try:
        return assert_process_accessible(
            k,
            process_id,
            session_id=session_id,
            require_session=require_session,
        )
    except ValueError as e:
        code = 404 if "not found" in str(e).lower() else 403
        raise HTTPException(status_code=code, detail=str(e)) from e


@router.get("/collab/{process_id}")
async def collab_get(
    process_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    session_id: str | None = Query(None, description="会话绑定（交互进程必填）"),
):
    """人机协作状态：交互进程必须带匹配 session_id。"""
    from backend.kernel import get_kernel

    k = get_kernel()
    _require_process(k, process_id, session_id=session_id, require_session=True)
    if hasattr(k, "_call"):
        return await k._acall("collab_get", {"process_id": process_id}) or {}
    return {}


@router.post("/collab/plan")
async def collab_set_plan(
    body: CollabPlanBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    from backend.kernel import get_kernel

    k = get_kernel()
    if not hasattr(k, "_call"):
        raise HTTPException(status_code=503, detail="kernel host unavailable")
    _require_process(k, body.process_id, session_id=body.session_id, require_session=True)
    return await k._acall(
        "collab_set_plan",
        {"process_id": body.process_id, "steps": body.steps},
    ) or {}


@router.post("/collab/interrupt")
async def collab_interrupt(
    body: CollabInterruptBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    from backend.kernel import get_kernel

    k = get_kernel()
    if not hasattr(k, "_call"):
        raise HTTPException(status_code=503, detail="kernel host unavailable")
    _require_process(k, body.process_id, session_id=body.session_id, require_session=True)
    r = await k._acall(
        "collab_interrupt",
        {"process_id": body.process_id, "reason": body.reason},
    ) or {}
    try:
        await k.suspend_process(body.process_id, reason=body.reason or "collab_interrupt")
    except Exception:
        pass
    return r


@router.post("/collab/resume")
async def collab_resume(
    body: CollabInterruptBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    from backend.kernel import get_kernel

    k = get_kernel()
    if not hasattr(k, "_call"):
        raise HTTPException(status_code=503, detail="kernel host unavailable")
    _require_process(k, body.process_id, session_id=body.session_id, require_session=True)
    r = await k._acall("collab_resume", {"process_id": body.process_id}) or {}
    try:
        await k.resume_process(body.process_id)
    except Exception:
        pass
    return r


@router.post("/collab/approve")
async def collab_approve(
    body: CollabApprovalBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    from backend.kernel import get_kernel

    k = get_kernel()
    if not hasattr(k, "_call"):
        raise HTTPException(status_code=503, detail="kernel host unavailable")
    _require_process(k, body.process_id, session_id=body.session_id, require_session=True)
    aid = body.request_id
    return await k._acall(
        "collab_resolve_approval",
        {
            "process_id": body.process_id,
            "approval_id": aid,
            "id": aid,
            "request_id": aid,
            "approve": body.approve,
            "note": body.note,
        },
    ) or {}


@router.post("/eval/run")
async def eval_run_api(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    weekly: bool = Query(True, description="跑完后写入周报"),
):
    """债 #2：触发 Eval Harness 四套固定集并可选写周报。"""
    # 在线程中跑同步 eval，避免阻塞过久时可后续改后台任务
    import asyncio

    from backend.services.weekly_report import collect_weekly_report, persist_eval_run

    def _run():
        from scripts.tevarn_eval import (
            _connect,
            suite_coding,
            suite_long,
            suite_research,
            suite_safety,
        )

        k = _connect()
        results = [
            suite_coding(k),
            suite_research(k),
            suite_long(k),
            suite_safety(k),
        ]
        overall = sum(r["score"] for r in results) / max(1, len(results))
        import os

        threshold = float(os.environ.get("TEVARN_EVAL_THRESHOLD", "0.75") or 0.75)
        out = {
            "overall": round(overall, 4),
            "threshold": threshold,
            "suites": results,
            "pass": overall + 1e-9 >= threshold,
        }
        path = persist_eval_run(out)
        out["persisted"] = str(path)
        weekly_rep = None
        if weekly:
            weekly_rep = collect_weekly_report(k, eval_result=out, persist=True)
        return {"eval": out, "weekly": weekly_rep}

    return await asyncio.to_thread(_run)


@router.get("/wasm/status")
async def wasm_status_api(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """债 #4：WASM 运行时状态。"""
    from backend.kernel import get_kernel

    k = get_kernel()
    if hasattr(k, "_call"):
        return await k._acall("wasm_status") or {}
    return {}


@router.get("/wasm/explain")
async def wasm_explain_api(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    module_id: str | None = None,
):
    """E-04：WASM fuel/memory/ops 限额可解释说明。"""
    from backend.kernel import get_kernel

    k = get_kernel()
    if hasattr(k, "_call"):
        params: dict[str, Any] = {}
        if module_id:
            params["module_id"] = module_id
        return await k._acall("wasm_explain", params) or {}
    return {}


@router.post("/coding-profile/spawn")
async def coding_profile_spawn_api(
    body: dict[str, Any],
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """E-01：按 Coding Profile 一键 spawn 进程。"""
    from backend.kernel import get_kernel

    k = get_kernel()
    if not hasattr(k, "_call"):
        return {"ok": False, "error": "kernel_rpc_unavailable"}
    return (
        await k._acall(
            "coding_profile_spawn",
            {
                "identity": body.get("identity") or "main",
                "profile": body.get("profile") or body.get("id") or "engineering",
                "session_id": body.get("session_id"),
            },
        )
        or {}
    )


@router.get("/abi/compat")
async def abi_compat_api(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """E-03：ABI 兼容窗口与 break 计数。"""
    from backend.kernel import get_kernel

    k = get_kernel()
    if hasattr(k, "_call"):
        return await k._acall("abi_compat") or {}
    return {}


@router.post("/abi/negotiate")
async def abi_negotiate_api(
    body: dict[str, Any],
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """E-03：客户端 ABI 协商。"""
    from backend.kernel import get_kernel

    k = get_kernel()
    if not hasattr(k, "_call"):
        return {"compatible": False, "error": "kernel_rpc_unavailable"}
    client = body.get("client_abi") or body.get("abi") or ""
    return await k._acall("abi_negotiate", {"client_abi": client}) or {}


@router.post("/packages/require-secure")
async def pkg_require_secure_api(
    body: dict[str, Any],
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """E-06：强制生产签名密钥策略。"""
    from backend.kernel import get_kernel

    k = get_kernel()
    if not hasattr(k, "_call"):
        return {"ok": False, "error": "kernel_rpc_unavailable"}
    require = body.get("require")
    if require is None:
        require = body.get("require_secure", True)
    return await k._acall("pkg_set_require_secure", {"require": bool(require)}) or {}


@router.get("/packages/catalog")
async def kernel_pkg_catalog(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """Kernel 侧包市场 catalog（签名扫描状态）。"""
    from backend.kernel import get_kernel

    k = get_kernel()
    if hasattr(k, "_call"):
        return await k._acall("pkg_catalog") or {}
    return {"items": [], "count": 0}


@router.get("/exit_reasons/{code}")
async def exit_reason_help(
    code: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """P0.5 R4：退出码 → 用户文案与恢复入口。"""
    from backend.agent.exit_reasons import describe_exit_reason

    return describe_exit_reason(code)


@router.get("/recovery/{process_id}")
async def process_recovery(
    process_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """P0.5：恢复计划（snapshot + tail_hash，禁止 full_replay）。"""
    from backend.kernel import get_kernel

    k = get_kernel()
    plan: dict[str, Any] = {}
    if hasattr(k, "process_recovery_plan"):
        plan = k.process_recovery_plan(process_id) or {}
    elif hasattr(k, "_call"):
        plan = await k._acall("process_recovery_plan", {"process_id": process_id}) or {}
    return {
        "process_id": process_id,
        "plan": plan,
        "resume": f"/api/kernel/processes/{process_id}/resume",
        "full_replay_forbidden": True,
    }


@router.post("/checkpoints/{checkpoint_id}/restore")
async def restore_checkpoint(
    checkpoint_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """P0-D：回滚文件写前快照。"""
    from backend.kernel import get_kernel

    k = get_kernel()
    if not hasattr(k, "_call"):
        raise HTTPException(status_code=503, detail="Rust kernel host required for restore")
    try:
        return await k._acall("checkpoint_restore", {"checkpoint_id": checkpoint_id})
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ── 收件箱与日报（0.6 自主运转）─────────────────────────────────


@router.post("/inbox")
async def enqueue_inbox_item(
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """手动派活：给员工投递工单（人话错误，便于 FE 直接展示）。"""
    from backend.core.config import settings
    from backend.kernel.workforce import get_workforce_inbox

    inbox = get_workforce_inbox()
    if inbox is None:
        # 503：服务未装配，不是「空列表」
        hint = (
            "收件箱服务未启用。请确认 agent_kernel_persistence 与 agent_dispatcher_enabled 为 true，"
            "或设置 TEVARN_AIOS_PROFILE=aios-dev 后重启后端。"
        )
        if not bool(getattr(settings, "agent_dispatcher_enabled", True)):
            hint = "派活器已关闭（agent_dispatcher_enabled=false）。打开后重启即可派工单。"
        raise HTTPException(status_code=503, detail=hint)
    reg = _identity_registry()
    if reg is None:
        raise HTTPException(
            status_code=503,
            detail="员工编制层未启用（identity layer）。请打开 agent_kernel_enabled / persistence 后重试。",
        )
    iid = str(body.get("identity_id") or "").strip()
    if not iid:
        raise HTTPException(status_code=400, detail="请先选择要派活的员工（identity_id 不能为空）")
    instruction = str(body.get("instruction") or "").strip()
    if not instruction:
        raise HTTPException(status_code=400, detail="工单指令不能为空，请写清要员工做什么")

    ident = await reg.get(iid)
    if ident is None:
        raise HTTPException(
            status_code=404,
            detail="找不到该员工。可能已被归档，或列表已过期——请刷新员工列表后重选。",
        )
    if getattr(ident, "status", None) and ident.status != "active":
        raise HTTPException(
            status_code=400,
            detail=f"员工「{ident.name}」当前状态为 {ident.status}，无法接单。请先复职（resume）再派活。",
        )
    # 归属校验：有 user_id 的身份只能由主人投递
    if ident.user_id is not None and str(ident.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail=f"无权向员工「{ident.name}」派活（归属他人）")

    source = str(body.get("source") or "manual").strip() or "manual"
    # UI 派活统一记 manual（inbox 允许 cron/webhook/api/manual）
    if source not in ("cron", "webhook", "api", "manual"):
        source = "manual"
    try:
        item = await inbox.enqueue(
            iid,
            instruction,
            source=source,
            payload=body.get("payload"),
            priority=int(body.get("priority") or 0),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if item is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"工单被拒收：员工「{ident.name}」可能已停用/归档，"
                "或收件箱溢出策略丢弃了本单。请检查员工状态与队列深度。"
            ),
        )
    return {
        "id": str(item.id),
        "status": item.status,
        "identity_id": str(item.identity_id),
        "identity_name": ident.name,
        "message": f"已派给「{ident.name}」，状态 {item.status}（dispatcher 将自动领取）",
    }


@router.get("/inbox")
async def list_inbox_items(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    identity_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    from backend.kernel.workforce import get_workforce_inbox

    inbox = get_workforce_inbox()
    if inbox is None:
        raise HTTPException(
            status_code=503,
            detail="收件箱未启用。打开 dispatcher/persistence 或 TEVARN_AIOS_PROFILE=aios-dev 后重启。",
        )
    items = await inbox.list_items(identity_id=identity_id, status=status, limit=limit)
    reg = _identity_registry()
    name_cache: dict[str, str] = {}
    out = []
    for i in items:
        iid = str(i.identity_id)
        iname = name_cache.get(iid)
        if iname is None and reg is not None:
            try:
                ident = await reg.get(iid)
                iname = str(getattr(ident, "name", "") or "") if ident else ""
            except Exception:
                iname = ""
            name_cache[iid] = iname or ""
        out.append(_inbox_item_public(i, identity_name=iname or None))
    return {
        "items": out,
        "total": len(out),
    }


@router.get("/inbox/dead")
async def list_dead_letters(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    limit: int = Query(50, ge=1, le=200),
):
    """死信台：达最大重试仍失败的工单（status=dead，兼容 failed）。"""
    from backend.kernel.workforce import get_workforce_inbox

    inbox = get_workforce_inbox()
    if inbox is None:
        raise HTTPException(status_code=503, detail="收件箱未启用")
    dead = await inbox.list_items(status="dead", limit=limit)
    failed = await inbox.list_items(status="failed", limit=limit)
    # 合并去重
    seen: set[str] = set()
    items = []
    for i in list(dead) + list(failed):
        sid = str(i.id)
        if sid in seen:
            continue
        seen.add(sid)
        items.append(i)
    items = items[:limit]
    return {
        "items": [
            {
                "id": str(i.id),
                "identity_id": str(i.identity_id),
                "instruction": (i.instruction or "")[:400],
                "status": i.status,
                "attempts": i.attempts,
                "error": (i.error or "")[:500],
                "process_id": getattr(i, "process_id", None),
                "finished_at": i.finished_at,
            }
            for i in items
        ],
        "total": len(items),
    }


@router.post("/inbox/{item_id}/requeue")
async def requeue_inbox_item(
    item_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """死信/失败工单重放 → pending，dispatcher 会再次领取。"""
    from backend.kernel.workforce import get_workforce_inbox

    inbox = get_workforce_inbox()
    if inbox is None:
        raise HTTPException(status_code=503, detail="收件箱未启用")
    item = await inbox.requeue(item_id, reset_attempts=True)
    if item is None:
        raise HTTPException(status_code=404, detail="工单不存在")
    return {
        "id": str(item.id),
        "status": item.status,
        "attempts": item.attempts,
        # audit-fix: 触顶时 requeue 返回 dead item，文案不得谎报"已重放"
        "message": (
            "已重放为 pending，dispatcher 将自动领取"
            if str(item.status) == "pending"
            else "requeue 次数已用尽，工单保持 dead，请人工介入"
        ),
    }


def _inbox_item_public(i: Any, *, identity_name: str | None = None) -> dict[str, Any]:
    """工单对外 JSON（进度卡 / 会话关联）。"""
    payload = getattr(i, "payload", None)
    if not isinstance(payload, dict):
        payload = {}
    err = str(getattr(i, "error", None) or "")
    result = str(getattr(i, "result", None) or "")
    budget_fail = bool(
        re_search_budget(err)
        or re_search_budget(result)
        or payload.get("budget_failed")
    )
    return {
        "id": str(i.id),
        "identity_id": str(i.identity_id),
        "identity_name": identity_name
        or str(payload.get("assigned_name") or payload.get("identity_name") or "")
        or None,
        "source": getattr(i, "source", None) or "",
        "instruction": (getattr(i, "instruction", None) or "")[:400],
        "status": i.status,
        "attempts": int(getattr(i, "attempts", 0) or 0),
        "result": result[:500],
        "error": err[:400],
        "process_id": getattr(i, "process_id", None),
        "created_at": i.created_at.isoformat() if getattr(i, "created_at", None) else None,
        "finished_at": getattr(i, "finished_at", None),
        "steward_session_id": str(payload.get("steward_session_id") or "") or None,
        "project_title": str(payload.get("project_title") or "") or None,
        "token_budget": payload.get("token_budget"),
        "budget_failed": budget_fail,
        "payload_via": str(payload.get("via") or "") or None,
    }


def re_search_budget(text: str) -> bool:
    t = (text or "").lower()
    if not t:
        return False
    keys = (
        "budget",
        "token 预算",
        "预算耗尽",
        "预算中断",
        "budgetexceeded",
        "kernel_token_budget",
        "kernel_budget_precheck",
        "额度用尽",
        "token_budget",
    )
    return any(k in t for k in keys)


@router.get("/sessions/{session_id}/workforce-jobs")
async def list_session_workforce_jobs(
    session_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    limit: int = Query(40, ge=1, le=100),
):
    """CEO 会话关联工单进度：payload.steward_session_id == session_id。

    供聊天页「工单进度卡」轮询；无关联时返回空列表（不 404）。
    """
    from backend.kernel.workforce import get_workforce_inbox

    sid = str(session_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="session_id required")
    # 会话归属
    try:
        import uuid as _uuid

        from backend.api.dependencies import assert_session_owner
        from backend.core.unit_of_work import UnitOfWork

        async with UnitOfWork() as uow:
            sess = await uow.sessions.get_by_id(_uuid.UUID(sid))
            if sess is None:
                raise HTTPException(status_code=404, detail="Session not found")
            assert_session_owner(getattr(sess, "user_id", None), current_user)
    except HTTPException:
        raise
    except Exception as e:
        logger.debug("session owner check soft-fail: %s", e)

    inbox = get_workforce_inbox()
    if inbox is None:
        return {"session_id": sid, "items": [], "total": 0, "enabled": False}

    # 拉近窗再按 steward_session_id 过滤（payload JSON 无索引）
    raw = await inbox.list_items(limit=min(200, max(limit * 4, 80)))
    reg = _identity_registry()
    name_cache: dict[str, str] = {}
    items_out: list[dict[str, Any]] = []
    for i in raw:
        payload = getattr(i, "payload", None)
        if not isinstance(payload, dict):
            payload = {}
        job_sid = str(payload.get("steward_session_id") or "").strip()
        if job_sid != sid:
            continue
        iid = str(i.identity_id)
        iname = name_cache.get(iid)
        if iname is None and reg is not None:
            try:
                ident = await reg.get(iid)
                iname = str(getattr(ident, "name", "") or "") if ident else ""
            except Exception:
                iname = ""
            name_cache[iid] = iname or ""
        items_out.append(_inbox_item_public(i, identity_name=iname or None))
        if len(items_out) >= limit:
            break

    # 统计
    by_status: dict[str, int] = {}
    budget_failed_n = 0
    for it in items_out:
        st = str(it.get("status") or "unknown")
        by_status[st] = by_status.get(st, 0) + 1
        if it.get("budget_failed"):
            budget_failed_n += 1

    return {
        "session_id": sid,
        "items": items_out,
        "total": len(items_out),
        "by_status": by_status,
        "budget_failed": budget_failed_n,
        "enabled": True,
    }


@router.post("/inbox/{item_id}/budget-retry")
async def budget_retry_inbox_item(
    item_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """预算失败一键：抬档案/本单预算 + requeue（或给在跑进程 top_up）。

    body: { amount?: int=300000, also_default?: bool=true, reason?: str }
    """
    from sqlalchemy import select

    from backend.agent.workforce_budget import clamp_ceo_budget, hard_cap
    from backend.kernel.workforce import get_workforce_inbox
    from backend.models.agent_identity import AgentInboxItem

    inbox = get_workforce_inbox()
    if inbox is None:
        raise HTTPException(status_code=503, detail="收件箱未启用")
    try:
        amount = int(body.get("amount") or 300_000)
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail="amount 必须为整数") from e
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount 必须为正")
    amount = clamp_ceo_budget(amount) if callable(clamp_ceo_budget) else min(amount, hard_cap())
    also_default = body.get("also_default", True) not in (False, "false", "0", 0, "no")
    reason = str(body.get("reason") or "ceo_budget_retry").strip()[:200]
    by = f"ceo:{getattr(current_user, 'id', current_user)}"

    # 取工单
    item = None
    try:
        async with inbox._session_factory() as session:  # type: ignore[attr-defined]
            item = (
                await session.execute(
                    select(AgentInboxItem).where(AgentInboxItem.id == __import__("uuid").UUID(str(item_id)))
                )
            ).scalar_one_or_none()
            if item is None:
                raise HTTPException(status_code=404, detail="工单不存在")
            iid = str(item.identity_id)
            await _require_identity_owner(iid, current_user)

            # 抬高本单 payload.token_budget
            payload = dict(item.payload or {}) if isinstance(item.payload, dict) else {}
            prev_tb = payload.get("token_budget")
            try:
                prev_n = int(prev_tb) if prev_tb is not None else 0
            except Exception:
                prev_n = 0
            # 新本单预算 = max(原, 原+amount/2, amount*2) 夹 hard_cap
            new_tb = max(prev_n + amount, amount * 2, 200_000)
            try:
                cap = int(hard_cap() or 2_000_000)
            except Exception:
                cap = 2_000_000
            if cap > 0:
                new_tb = min(new_tb, cap)
            payload["token_budget"] = new_tb
            payload["budget_source"] = "ceo_budget_retry"
            payload["budget_retry_at"] = time.time()
            payload["budget_retry_by"] = by
            payload.pop("budget_failed", None)
            item.payload = payload
            await session.commit()
            await session.refresh(item)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("budget-retry load item: %s", e)
        raise HTTPException(status_code=500, detail=f"读取工单失败: {e}") from e

    iid = str(item.identity_id)
    default_updated = None
    if also_default:
        reg = _identity_registry()
        if reg is not None:
            try:
                ident = await reg.get(iid)
                if ident is not None:
                    cur = int(getattr(ident, "default_token_budget", 0) or 0)
                    new_def = cur + amount if cur > 0 else max(amount, 200_000)
                    try:
                        cap = int(hard_cap() or 2_000_000)
                        if cap > 0:
                            new_def = min(new_def, cap)
                    except Exception:
                        pass
                    await reg.update_profile(iid, default_token_budget=new_def)
                    default_updated = new_def
            except Exception as e:
                default_updated = f"error:{e}"

    # 在跑进程 top_up
    kernel = get_kernel()
    process_results: list[dict[str, Any]] = []
    for key in (f"wf:{iid}", iid):
        try:
            live = kernel.live_processes_for_identity(key)
        except Exception:
            live = []
        for p in live:
            try:
                process_results.append(
                    kernel.top_up_budget(
                        p.id,
                        amount,
                        by=by,
                        reason=reason or "budget_retry",
                    )
                )
            except Exception as e:
                process_results.append(
                    {"ok": False, "process_id": getattr(p, "id", None), "error": str(e)[:200]}
                )

    # 失败/死信 → requeue；在途则只加预算
    requeued = False
    new_status = str(item.status)
    if str(item.status) in ("dead", "failed", "dropped"):
        rq = await inbox.requeue(item_id, reset_attempts=True)
        if rq is not None:
            # audit-fix: requeue 触顶返回 status="dead" 的 item（非 None），
            # 只有真的回到 pending 才算重派成功，避免向 CEO 谎报
            requeued = str(rq.status) == "pending"
            new_status = str(rq.status)
            # requeue 可能清掉 payload 以外字段；再写一次 token_budget
            try:
                async with inbox._session_factory() as session:  # type: ignore[attr-defined]
                    row = (
                        await session.execute(
                            select(AgentInboxItem).where(
                                AgentInboxItem.id == __import__("uuid").UUID(str(item_id))
                            )
                        )
                    ).scalar_one_or_none()
                    if row is not None:
                        pl = dict(row.payload or {}) if isinstance(row.payload, dict) else {}
                        pl["token_budget"] = payload.get("token_budget")
                        pl["budget_source"] = "ceo_budget_retry"
                        row.payload = pl
                        await session.commit()
            except Exception as e:
                logger.debug("budget-retry rewrite payload: %s", e)

    return {
        "ok": True,
        "id": str(item_id),
        "status": new_status,
        "requeued": requeued,
        "amount": amount,
        "token_budget": payload.get("token_budget"),
        "default_token_budget": default_updated,
        "processes": process_results,
        "message": (
            f"已追加预算 +{amount}"
            + (f"，本单 token_budget={payload.get('token_budget')}" if payload.get("token_budget") else "")
            + ("，工单已重派 pending" if requeued else ("，requeue 次数已用尽，工单保持 dead，请人工介入" if new_status == "dead" else "（在途进程已 top_up）"))
        ),
    }


@router.post("/inbox/{item_id}/discard")
async def discard_inbox_item(
    item_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """丢弃死信（dropped）。"""
    from backend.kernel.workforce import get_workforce_inbox

    inbox = get_workforce_inbox()
    if inbox is None:
        raise HTTPException(status_code=503, detail="收件箱未启用")
    ok = await inbox.discard_dead(item_id)
    if not ok:
        raise HTTPException(status_code=400, detail="仅 dead/failed 可丢弃，或工单不存在")
    return {"discarded": True, "id": item_id}


@router.get("/jobs/running")
async def list_running_jobs(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """统一「现在在跑什么」：claimed 工单 + 非终态 kernel process。"""
    from backend.kernel.workforce import get_workforce_inbox

    kernel = get_kernel()
    inbox = get_workforce_inbox()
    claimed = []
    if inbox is not None:
        for i in await inbox.list_items(status="claimed", limit=50):
            pid = getattr(i, "process_id", None)
            claimed.append(
                {
                    "kind": "inbox",
                    "id": str(i.id),
                    "job_id": str(i.id),  # Run 关联：工单 ID
                    "identity_id": str(i.identity_id),
                    "instruction": (i.instruction or "")[:200],
                    "status": i.status,
                    "process_id": pid,
                    "attempts": i.attempts,
                    # 统一关联模型（chat/inbox/process）
                    "run_ref": {
                        "job_id": str(i.id),
                        "process_id": str(pid) if pid else None,
                        "identity_id": str(i.identity_id),
                        "source": "inbox",
                    },
                }
            )
    live_procs = []
    for p in kernel.list_processes(include_terminal=False):
        d = p.to_dict() if hasattr(p, "to_dict") else {}
        st = d.get("state") or getattr(p, "state", "")
        if st in ("completed", "failed", "killed", "interrupted", "exited", "done"):
            continue
        meta = d.get("meta") or getattr(p, "meta", None) or {}
        if not isinstance(meta, dict):
            meta = {}
        job_id = meta.get("inbox_item_id") or meta.get("job_id")
        identity_id = meta.get("identity_id")
        pid = d.get("id") or getattr(p, "id", None)
        live_procs.append(
            {
                "kind": "process",
                "id": pid,
                "process_id": pid,
                "identity": d.get("identity") or getattr(p, "identity_key", None),
                "state": st,
                "session_id": d.get("session_id"),
                "tokens_used": d.get("tokens_used", 0),
                "job_id": job_id,
                "identity_id": identity_id,
                "run_ref": {
                    "process_id": str(pid) if pid else None,
                    "job_id": str(job_id) if job_id else None,
                    "session_id": d.get("session_id"),
                    "identity_id": str(identity_id) if identity_id else None,
                    "source": "kernel_process",
                },
            }
        )
    return {
        "inbox_claimed": claimed,
        "processes": live_procs,
        "total": len(claimed) + len(live_procs),
        "run_model": "job_id|process_id|session_id|identity_id",
    }


@router.get("/workspace/brief")
async def workspace_brief(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    hours: int = Query(24, ge=1, le=24 * 14),
):
    """AI 公司「晨报」聚合：完成/失败/待办/在跑/待批 + 最近产出。

    供驾驶舱 Workspace 与 CLI 一请求消费；不替代明细 API。
    """
    import time as _time

    from backend.kernel.workforce import build_daily_report, get_workforce_inbox

    kernel = get_kernel()
    inbox = get_workforce_inbox()
    reg = _identity_registry()
    if reg is not None and (
        not hasattr(kernel, "identity_registry") or kernel.identity_registry is None
    ):
        try:
            kernel.identity_registry = reg  # type: ignore[attr-defined]
        except Exception:
            pass

    crew = []
    if reg is not None:
        try:
            for ident in await reg.list(status="active"):
                crew.append(
                    {
                        "id": str(ident.id),
                        "name": ident.name,
                        "role": ident.role,
                        "status": ident.status,
                    }
                )
        except Exception:
            crew = []

    report: dict[str, Any] = {}
    if inbox is not None:
        try:
            report = await build_daily_report(kernel, inbox, hours=hours, identity_id=None)
        except Exception as e:
            logger.debug("workspace brief report: %s", e)
            report = {}

    stats = (report.get("inbox") or {}).get("stats") or {}
    done = int(stats.get("done") or 0)
    failed = int(stats.get("failed") or 0) + int(stats.get("dead") or 0)
    pending = int(stats.get("pending") or 0)
    claimed = int(stats.get("claimed") or 0)

    live_jobs = 0
    live_names: list[str] = []
    if inbox is not None:
        try:
            for i in await inbox.list_items(status="claimed", limit=20):
                live_jobs += 1
                nm = next((c["name"] for c in crew if c["id"] == str(i.identity_id)), None)
                if nm and nm not in live_names:
                    live_names.append(nm)
        except Exception:
            pass
    for p in kernel.list_processes(include_terminal=False):
        d = p.to_dict() if hasattr(p, "to_dict") else {}
        st = d.get("state") or ""
        if st in ("completed", "failed", "killed", "interrupted", "exited", "done"):
            continue
        ident = d.get("identity") or ""
        if ident and ident not in live_names and not str(ident).startswith("sub:"):
            live_names.append(str(ident)[:32])

    esc_pending = len(kernel.list_escalations(status="pending"))
    evo_pending = 0
    try:
        from backend.kernel.workforce import get_evolution_engine

        eng = get_evolution_engine()
        if eng is not None and hasattr(eng, "list_proposals"):
            props = await eng.list_proposals(status="pending")
            evo_pending = len(props or [])
    except Exception:
        evo_pending = 0

    recent_done = (report.get("inbox") or {}).get("recent_done") or []
    recent_failed = (report.get("inbox") or {}).get("recent_failed") or []

    return {
        "kind": "workspace_brief",
        "hours": hours,
        "ts": _time.time(),
        "headline": {
            "crew_active": len(crew),
            "jobs_done": done,
            "jobs_failed": failed,
            "jobs_pending": pending,
            "jobs_running": max(claimed, live_jobs),
            "approvals_pending": esc_pending + evo_pending,
            "escalations_pending": esc_pending,
            "evolution_pending": evo_pending,
        },
        "running_employees": live_names[:12],
        "recent_done": recent_done[:8],
        "recent_failed": recent_failed[:5],
        "crew": crew[:24],
        "product_concepts": ["employee", "job", "approval"],
        "narrative": {
            "zh": (
                f"近 {hours}h：完成 {done} 单 · 失败/死信 {failed} · "
                f"在跑 {max(claimed, live_jobs)} · 待批 {esc_pending + evo_pending}"
            ),
            "en": (
                f"Last {hours}h: {done} done · {failed} failed · "
                f"{max(claimed, live_jobs)} running · {esc_pending + evo_pending} awaiting you"
            ),
        },
    }


@router.post("/jobs/stop")
async def stop_job(
    body: StopJobBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """E4 统一停止语义：取消 process + agent loop + 工单 cancelled。

    优先走 dispatcher.cancel_job；无 dispatcher 时仍能 kill process / cancel 工单。
    """
    from backend.kernel.workforce import get_workforce_dispatcher, get_workforce_inbox

    item_id = (body.inbox_item_id or "").strip() or None
    process_id = (body.process_id or "").strip() or None
    if not item_id and not process_id:
        raise HTTPException(
            status_code=400,
            detail="请提供 inbox_item_id 或 process_id（至少一个）",
        )

    reason = (body.reason or "stopped by user").strip()[:500]
    # 有 process_id 时校验归属
    if process_id:
        try:
            await _require_process_owner(process_id, current_user)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    disp = get_workforce_dispatcher()
    if disp is not None:
        result = await disp.cancel_job(
            item_id=item_id, process_id=process_id, reason=reason
        )
        return {"ok": bool(result.get("ok")), **result}

    # 降级：无 dispatcher 时直接 kill + cancel
    out: dict[str, Any] = {
        "ok": False,
        "inbox_item_id": item_id,
        "process_id": process_id,
        "loop_stopped": False,
        "task_cancelled": False,
        "process_killed": False,
        "inbox_cancelled": False,
        "reason": reason,
        "fallback": True,
    }
    kernel = get_kernel()
    if process_id:
        try:
            ended = await kernel.end_process(
                process_id, state="killed", reason=reason
            )
            out["process_killed"] = ended is not None
        except Exception as e:
            logger.debug("stop_job end_process: %s", e)
    inbox = get_workforce_inbox()
    if item_id and inbox is not None:
        try:
            cancelled = await inbox.cancel(
                item_id, reason=reason, process_id=process_id
            )
            out["inbox_cancelled"] = cancelled is not None
        except Exception as e:
            logger.debug("stop_job inbox.cancel: %s", e)
    out["ok"] = bool(out["process_killed"] or out["inbox_cancelled"])
    return out


async def _load_report_read_at() -> float | None:
    try:
        from backend.repositories.setting_repo import AsyncSettingRepository

        row = await AsyncSettingRepository().get_by_key(_REPORT_READ_KEY)
        if row is None or row.value is None:
            return None
        return float(row.value)
    except Exception as e:
        logger.debug("report read_at load: %s", e)
        return None


@router.get("/workforce/report")
async def workforce_report(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    hours: int = Query(24, ge=1, le=24 * 90),
    identity_id: str | None = Query(None, description="只看该员工的汇报"),
    identity_name: str | None = Query(None, description="按员工名过滤（与 id 二选一）"),
):
    """「你不在的这段时间」——workforce 工作汇报。

    不传 identity → 全队；传 identity_id/name → 单员工（各聊天会话内容应不同）。
    含 marked_read_at / has_unread（日报一键已读）。
    """
    from backend.kernel.workforce import build_daily_report, get_workforce_inbox

    inbox = get_workforce_inbox()
    if inbox is None:
        raise HTTPException(
            status_code=503,
            detail="日报依赖收件箱服务。请启用 workforce dispatcher 后重试。",
        )
    scope = (identity_id or "").strip() or None
    name = (identity_name or "").strip()
    if not scope and name:
        reg = _identity_registry()
        if reg is not None:
            for ident in await reg.list(status=None):
                if ident.name == name:
                    scope = str(ident.id)
                    break
    kernel = get_kernel()
    # 给 report 拼名字用
    if not hasattr(kernel, "identity_registry") or getattr(kernel, "identity_registry", None) is None:
        try:
            kernel.identity_registry = _identity_registry()  # type: ignore[attr-defined]
        except Exception:
            pass
    report = await build_daily_report(kernel, inbox, hours=hours, identity_id=scope)
    read_at = await _load_report_read_at()
    report["marked_read_at"] = read_at
    # 有完成/失败且晚于已读时间 → 未读
    has_unread = False
    try:
        recent = list(report.get("inbox", {}).get("recent_done") or []) + list(
            report.get("inbox", {}).get("recent_failed") or []
        )
        if read_at is None:
            has_unread = len(recent) > 0 or int(
                (report.get("inbox") or {}).get("stats", {}).get("done") or 0
            ) > 0
        else:
            for it in recent:
                fa = it.get("finished_at")
                if fa is not None and float(fa) > float(read_at):
                    has_unread = True
                    break
    except Exception:
        has_unread = False
    report["has_unread"] = has_unread
    return report


@router.post("/workforce/report/read")
async def mark_workforce_report_read(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """日报/周报一键已读：写入 settings.workforce_report_read_at。"""
    now = time.time()
    try:
        from backend.repositories.setting_repo import AsyncSettingRepository

        await AsyncSettingRepository().upsert(
            _REPORT_READ_KEY,
            now,
            category="workforce",
            description="日报最后已读时间（unix ts）",
        )
    except Exception as e:
        logger.warning("mark report read failed: %s", e)
        raise HTTPException(status_code=500, detail=f"无法写入已读状态: {e}") from e
    return {"ok": True, "marked_read_at": now}


@router.post("/workforce/seed-template-crew")
async def seed_template_crew_api(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """补全默认编制：CEO + 自动入编同事模板（幂等）。"""
    from backend.scripts.seed_template_crew import seed_template_crew

    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="身份注册表未启用")
    result = await seed_template_crew(
        reg, user_id=current_user.id, include_workers=True
    )
    return result


@router.get("/workforce/hire-templates")
async def list_hire_templates_api(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """同事模板目录：前端「一键起新员工」用。"""
    from backend.scripts.seed_template_crew import list_hire_templates

    templates = list_hire_templates()
    return {"templates": templates, "total": len(templates)}


@router.post("/workforce/hire-from-template")
async def hire_from_template_api(
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """从模板一键雇佣（重名自动加后缀）。

    body: { template_id: str, name?: str }
    """
    from backend.scripts.seed_template_crew import hire_from_template

    reg = _identity_registry()
    if reg is None:
        raise HTTPException(status_code=503, detail="身份注册表未启用")
    tid = str(body.get("template_id") or "").strip()
    if not tid:
        raise HTTPException(status_code=400, detail="template_id required")
    name = body.get("name")
    name_s = str(name).strip() if name is not None else None
    result = await hire_from_template(
        reg, tid, user_id=current_user.id, name=name_s or None
    )
    if not result.get("ok"):
        raise HTTPException(
            status_code=400, detail=result.get("error") or "hire failed"
        )
    return result


@router.get("/policy/decisions")
async def list_policy_decisions(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    limit: int = Query(100, ge=1, le=500),
    outcome: str | None = Query(None, description="allow|deny|escalate"),
):
    """0.5.2 权限一张网：policy.decision 事件只读列表。

    who / what / outcome / source 来自 kernel 事件流（与 mediation 并存）。
    """
    kernel = get_kernel()
    events = kernel.events(kind="policy.decision", limit=limit)
    items = []
    for e in events:
        d = e.to_dict() if hasattr(e, "to_dict") else dict(e)
        detail = d.get("detail") or d.get("data") or {}
        if isinstance(detail, dict):
            oc = detail.get("outcome")
            if outcome and oc != outcome:
                continue
            items.append(
                {
                    "ts": d.get("ts"),
                    "process_id": d.get("process_id"),
                    "hash": d.get("hash"),
                    "who": detail.get("who"),
                    "what": detail.get("what"),
                    "action": detail.get("action"),
                    "target": detail.get("target"),
                    "outcome": oc,
                    "reason": detail.get("reason"),
                    "source": detail.get("source", "kernel"),
                }
            )
        else:
            items.append(d)
    return {"decisions": items, "total": len(items)}


@router.post("/backup/export")
async def export_aios_backup(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """0.6 L4：一键导出编制/工单/记忆/审计摘要（私有线防盘挂）。

    返回 JSON 快照（不含明文密钥）。大库时条目有上限截断。
    """
    import time as _time

    from sqlalchemy import select

    from backend.core.config import settings
    from backend.database import AsyncSessionLocal

    kernel = get_kernel()
    reg = _identity_registry()
    from backend.kernel.workforce import get_workforce_inbox

    inbox = get_workforce_inbox()

    identities_out: list[dict] = []
    memories_out: list[dict] = []
    if reg is not None:
        for ident in await reg.list(status=None):
            identities_out.append(
                {
                    "id": str(ident.id),
                    "name": ident.name,
                    "role": getattr(ident, "role", "") or "",
                    "status": getattr(ident, "status", "active"),
                    "capabilities": list(getattr(ident, "capabilities", None) or []),
                    "default_token_budget": getattr(ident, "default_token_budget", None),
                }
            )
            try:
                mems = await reg.current_memory(ident.id)
                for m in mems[:50]:
                    memories_out.append(
                        {
                            "identity_id": str(ident.id),
                            "identity_name": ident.name,
                            "kind": m.kind,
                            "content": (m.content or "")[:2000],
                            "source": getattr(m, "source", None),
                            "version": getattr(m, "version", 1),
                        }
                    )
            except Exception as e:
                logger.debug("backup memory skip %s: %s", ident.name, e)

    inbox_out: list[dict] = []
    if inbox is not None:
        for st in ("pending", "claimed", "done", "failed", "dead"):
            for i in await inbox.list_items(status=st, limit=100):
                inbox_out.append(
                    {
                        "id": str(i.id),
                        "identity_id": str(i.identity_id),
                        "status": i.status,
                        "instruction": (i.instruction or "")[:500],
                        "attempts": i.attempts,
                        "process_id": getattr(i, "process_id", None),
                        "result": (getattr(i, "result", None) or "")[:500]
                        if st in ("done", "failed", "dead")
                        else None,
                        "error": (getattr(i, "error", None) or "")[:300]
                        if st in ("failed", "dead")
                        else None,
                    }
                )

    # 会话摘要（仅当前用户，截断）
    sessions_out: list[dict] = []
    try:
        from backend.models.session import Session as ChatSession

        async with AsyncSessionLocal() as session:
            q = (
                select(ChatSession)
                .where(ChatSession.user_id == current_user.id)
                .order_by(ChatSession.updated_at.desc())
                .limit(200)
            )
            rows = list((await session.execute(q)).scalars().all())
            for s in rows:
                cfg = getattr(s, "config", None) or {}
                sessions_out.append(
                    {
                        "id": str(s.id),
                        "status": str(getattr(s, "status", "") or ""),
                        "identity_hint": (cfg.get("identity") or "")[:120]
                        if isinstance(cfg, dict)
                        else "",
                        "source_hint": (cfg.get("source") or cfg.get("kind") or "")
                        if isinstance(cfg, dict)
                        else "",
                        "updated_at": str(getattr(s, "updated_at", "") or ""),
                    }
                )
    except Exception as e:
        logger.warning("backup sessions: %s", e)

    # 审计尾部
    events_tail = []
    try:
        for e in kernel.events(limit=200):
            events_tail.append(e.to_dict() if hasattr(e, "to_dict") else dict(e))
    except Exception as e:
        logger.debug("backup events: %s", e)

    audit_path = None
    try:
        store = getattr(kernel, "_audit_store", None) or getattr(kernel, "audit_store", None)
        if store is not None:
            audit_path = getattr(store, "path", None)
    except Exception:
        pass

    db_url = str(getattr(settings, "db_url", "") or "")
    # 脱敏：去掉可能的密码
    if "@" in db_url and "://" in db_url:
        try:
            scheme, rest = db_url.split("://", 1)
            if "@" in rest:
                creds, hostpart = rest.rsplit("@", 1)
                db_url_safe = f"{scheme}://***@{hostpart}"
            else:
                db_url_safe = db_url
        except Exception:
            db_url_safe = "redacted"
    else:
        db_url_safe = db_url

    return {
        "exported_at": _time.time(),
        "exported_by": str(current_user.id),
        "version": "0.4.6-alpha-backup-v1",
        "db_url_hint": db_url_safe,
        "audit_path": audit_path,
        "identities": identities_out,
        "identity_memories": memories_out,
        "inbox_items": inbox_out,
        "sessions": sessions_out,
        "kernel_events_tail": events_tail,
        "counts": {
            "identities": len(identities_out),
            "memories": len(memories_out),
            "inbox": len(inbox_out),
            "sessions": len(sessions_out),
            "events": len(events_tail),
        },
    }


# ── 项目组（企业 IM 群：派活聚合进度）──────────────────────────


@router.get("/project-groups")
async def list_project_groups(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    from sqlalchemy import select

    from backend.database import AsyncSessionLocal
    from backend.models.project_group import ProjectGroup

    async with AsyncSessionLocal() as session:
        q = (
            select(ProjectGroup)
            .where(
                (ProjectGroup.user_id == current_user.id)
                | (ProjectGroup.user_id.is_(None))
            )
            .order_by(ProjectGroup.updated_at.desc())
            .limit(limit)
        )
        if status:
            q = q.where(ProjectGroup.status == status)
        rows = list((await session.execute(q)).scalars().all())
    return {
        "groups": [_project_group_summary(g) for g in rows],
        "total": len(rows),
    }


@router.post("/project-groups")
async def create_project_group(
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """创建项目组：title + members[{id,name}] + tasks[{inbox_item_id,identity_id,name}]。"""
    from backend.database import AsyncSessionLocal
    from backend.models.project_group import ProjectGroup

    title = str(body.get("title") or "").strip() or "项目组"
    members = body.get("members") if isinstance(body.get("members"), list) else []
    tasks = body.get("tasks") if isinstance(body.get("tasks"), list) else []
    created_by = str(body.get("created_by") or "user")
    async with AsyncSessionLocal() as session:
        g = ProjectGroup(
            user_id=current_user.id,
            title=title[:200],
            status="open",
            created_by=created_by[:32],
            members=members,
            tasks=tasks,
            summary=str(body.get("summary") or "")[:2000],
            meta=body.get("meta") if isinstance(body.get("meta"), dict) else {},
        )
        session.add(g)
        await session.commit()
        await session.refresh(g)
        return _project_group_detail(g)


@router.get("/project-groups/{group_id}")
async def get_project_group(
    group_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """项目组详情：成员 + 各工单最新状态（从 inbox 实时拼）。"""
    import uuid as _u

    from sqlalchemy import select

    from backend.database import AsyncSessionLocal
    from backend.kernel.workforce import get_workforce_inbox
    from backend.models.project_group import ProjectGroup

    try:
        gid = _u.UUID(str(group_id))
    except Exception as e:
        raise HTTPException(status_code=400, detail="invalid group id") from e

    async with AsyncSessionLocal() as session:
        g = (
            await session.execute(select(ProjectGroup).where(ProjectGroup.id == gid))
        ).scalar_one_or_none()
        if g is None:
            raise HTTPException(status_code=404, detail="project group not found")

    detail = _project_group_detail(g)
    inbox = get_workforce_inbox()
    task_views = []
    # 按 identity 缓存近期工单，避免全表扫
    cache: dict[str, list] = {}
    for t in list(g.tasks or []):
        iid = str(t.get("inbox_item_id") or "")
        ident_id = str(t.get("identity_id") or "")
        row = {
            "inbox_item_id": iid,
            "identity_id": ident_id,
            "identity_name": str(t.get("identity_name") or t.get("name") or ""),
            "status": "unknown",
            "instruction": "",
            "result": "",
            "error": "",
            "finished_at": None,
        }
        if inbox is not None and (ident_id or iid):
            try:
                key = ident_id or "_all"
                if key not in cache:
                    cache[key] = await inbox.list_items(
                        identity_id=ident_id or None, limit=50
                    )
                hit = next((x for x in cache[key] if str(x.id) == iid), None)
                if hit is None and not ident_id:
                    if "_all" not in cache:
                        cache["_all"] = await inbox.list_items(limit=100)
                    hit = next((x for x in cache["_all"] if str(x.id) == iid), None)
                if hit:
                    row["status"] = hit.status
                    row["instruction"] = (hit.instruction or "")[:400]
                    row["result"] = (hit.result or "")[:1500]
                    row["error"] = (hit.error or "")[:400]
                    row["finished_at"] = hit.finished_at
                    if not row["identity_id"]:
                        row["identity_id"] = str(hit.identity_id)
            except Exception:
                pass
        task_views.append(row)
    detail["task_views"] = task_views
    stats: dict[str, int] = {}
    for tv in task_views:
        st = tv.get("status") or "unknown"
        stats[st] = stats.get(st, 0) + 1
    detail["progress"] = stats
    return detail


@router.post("/project-groups/{group_id}/tasks")
async def attach_project_group_tasks(
    group_id: str,
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """向项目组追加工单引用。"""
    import uuid as _u

    from sqlalchemy import select

    from backend.database import AsyncSessionLocal
    from backend.models.project_group import ProjectGroup

    try:
        gid = _u.UUID(str(group_id))
    except Exception as e:
        raise HTTPException(status_code=400, detail="invalid group id") from e
    tasks_in = body.get("tasks") if isinstance(body.get("tasks"), list) else []
    async with AsyncSessionLocal() as session:
        g = (
            await session.execute(select(ProjectGroup).where(ProjectGroup.id == gid))
        ).scalar_one_or_none()
        if g is None:
            raise HTTPException(status_code=404, detail="project group not found")
        cur = list(g.tasks or [])
        seen = {str(t.get("inbox_item_id")) for t in cur}
        for t in tasks_in:
            iid = str(t.get("inbox_item_id") or "")
            if not iid or iid in seen:
                continue
            cur.append(
                {
                    "inbox_item_id": iid,
                    "identity_id": str(t.get("identity_id") or ""),
                    "identity_name": str(t.get("identity_name") or t.get("name") or ""),
                }
            )
            seen.add(iid)
        g.tasks = cur
        if isinstance(body.get("members"), list) and body["members"]:
            g.members = body["members"]
        await session.commit()
        await session.refresh(g)
        return _project_group_detail(g)


@router.delete("/project-groups/{group_id}")
async def delete_project_group(
    group_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """删除项目组聚合视图（不删 inbox 工单真源）。"""
    import uuid as _u

    from sqlalchemy import select

    from backend.database import AsyncSessionLocal
    from backend.models.project_group import ProjectGroup

    try:
        gid = _u.UUID(str(group_id))
    except Exception as e:
        raise HTTPException(status_code=400, detail="invalid group id") from e

    async with AsyncSessionLocal() as session:
        g = (
            await session.execute(select(ProjectGroup).where(ProjectGroup.id == gid))
        ).scalar_one_or_none()
        if g is None:
            raise HTTPException(status_code=404, detail="project group not found")
        # 仅允许删自己的，或无 user_id 的历史遗留组
        owner = getattr(g, "user_id", None)
        if owner is not None and str(owner) != str(current_user.id):
            raise HTTPException(status_code=403, detail="not your project group")
        title = str(g.title or "")
        await session.delete(g)
        await session.commit()
        return {"deleted": True, "id": str(gid), "title": title}


def _project_group_summary(g) -> dict:
    tasks = list(g.tasks or [])
    members = list(g.members or [])
    return {
        "id": str(g.id),
        "title": g.title,
        "status": g.status,
        "created_by": g.created_by,
        "member_count": len(members),
        "task_count": len(tasks),
        "summary": (g.summary or "")[:200],
        "created_at": g.created_at.isoformat() if g.created_at else None,
        "updated_at": g.updated_at.isoformat() if g.updated_at else None,
    }


def _project_group_detail(g) -> dict:
    d = _project_group_summary(g)
    d["members"] = list(g.members or [])
    d["tasks"] = list(g.tasks or [])
    d["meta"] = g.meta if isinstance(g.meta, dict) else {}
    return d


# ── 受控进化（0.7）───────────────────────────────────────────


def _proposal_dict(p) -> dict:
    return {
        "id": str(p.id),
        "identity_id": str(p.identity_id),
        "kind": p.kind,
        "title": p.title,
        "rationale": p.rationale,
        "payload": p.payload or {},
        "status": p.status,
        "resolved_by": p.resolved_by,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "applied_at": p.applied_at,
        "rolled_back_at": p.rolled_back_at,
    }


def _evo_include_orphan() -> bool:
    from backend.core.config import settings

    return bool(getattr(settings, "single_user_mode", True))


@router.get("/evolution/proposals")
async def list_evolution_proposals(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    identity_id: str | None = Query(None),
    status: str | None = Query(None),
):
    from backend.kernel.workforce import get_evolution_engine

    eng = get_evolution_engine()
    if eng is None:
        raise HTTPException(status_code=503, detail="evolution engine 未启用")
    # 多租户：只列当前用户 Identity 的提案（单用户模式附带 orphan）
    items = await eng.list_proposals(
        identity_id=identity_id,
        status=status,
        user_id=current_user.id,
        include_orphan=_evo_include_orphan(),
    )
    return {"proposals": [_proposal_dict(p) for p in items], "total": len(items)}


@router.post("/evolution/analyze")
async def run_evolution_analyze(
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """手动触发述职分析（生成 pending 建议，永不自动应用）。"""
    from backend.kernel.workforce import get_evolution_engine

    eng = get_evolution_engine()
    if eng is None:
        raise HTTPException(status_code=503, detail="evolution engine 未启用")
    iid = str(body.get("identity_id") or "").strip()
    if not iid:
        raise HTTPException(status_code=400, detail="identity_id required")
    # 归属：只能分析自己的员工
    try:
        reg = _identity_registry()
        if reg is not None:
            ident = await reg.get(iid)
            if ident is None:
                raise HTTPException(status_code=404, detail="identity not found")
            owner = getattr(ident, "user_id", None)
            if owner is not None and str(owner) != str(current_user.id):
                raise HTTPException(status_code=403, detail="not your identity")
            if owner is None and not _evo_include_orphan():
                raise HTTPException(status_code=403, detail="orphan identity blocked")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        proposals = await eng.analyze(iid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"generated": len(proposals), "proposals": [_proposal_dict(p) for p in proposals]}


@router.post("/evolution/proposals/{proposal_id}/approve")
async def approve_evolution(
    proposal_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    from backend.kernel.workforce import get_evolution_engine

    eng = get_evolution_engine()
    if eng is None:
        raise HTTPException(status_code=503, detail="evolution engine 未启用")
    try:
        await eng.assert_proposal_owner(
            proposal_id, current_user.id, include_orphan=_evo_include_orphan()
        )
        p = await eng.approve(proposal_id, by=str(current_user.id))
    except ValueError as e:
        msg = str(e)
        code = 403 if ("无权" in msg or "归属" in msg) else 400
        raise HTTPException(status_code=code, detail=msg) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"apply failed: {e}") from e
    return _proposal_dict(p)


@router.post("/evolution/proposals/{proposal_id}/reject")
async def reject_evolution(
    proposal_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    from backend.kernel.workforce import get_evolution_engine

    eng = get_evolution_engine()
    if eng is None:
        raise HTTPException(status_code=503, detail="evolution engine 未启用")
    try:
        await eng.assert_proposal_owner(
            proposal_id, current_user.id, include_orphan=_evo_include_orphan()
        )
        p = await eng.reject(proposal_id, by=str(current_user.id))
    except ValueError as e:
        msg = str(e)
        code = 403 if ("无权" in msg or "归属" in msg) else 400
        raise HTTPException(status_code=code, detail=msg) from e
    return _proposal_dict(p)


@router.post("/evolution/proposals/{proposal_id}/rollback")
async def rollback_evolution(
    proposal_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    from backend.kernel.workforce import get_evolution_engine

    eng = get_evolution_engine()
    if eng is None:
        raise HTTPException(status_code=503, detail="evolution engine 未启用")
    try:
        await eng.assert_proposal_owner(
            proposal_id, current_user.id, include_orphan=_evo_include_orphan()
        )
        p = await eng.rollback(proposal_id, by=str(current_user.id))
    except ValueError as e:
        msg = str(e)
        code = 403 if ("无权" in msg or "归属" in msg) else 400
        raise HTTPException(status_code=code, detail=msg) from e
    return _proposal_dict(p)


@router.get("/workforce/org")
async def workforce_org(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """汇报线观察 + 组织预算聚合（从 parent 链涌现，只读视图）。"""
    from backend.database import AsyncSessionLocal
    from backend.kernel.workforce import build_org_view

    return await build_org_view(AsyncSessionLocal)


@router.get("/approval-rules")
async def get_approval_rules_api(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """只读：当前生效的审批规则（与前端 settings/approval_rules 同源）。"""
    from backend.kernel.approval_rules import load_approval_rules

    rules = await load_approval_rules()
    return {"rules": rules, "total": len(rules)}
