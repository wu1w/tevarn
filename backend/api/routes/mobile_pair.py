"""
手机扫码配对 API —— 路径对齐 Flutter pair_apply 期望：

  POST /api/mobile/pair/start
  GET  /api/mobile/pair/status/{id}
  POST /api/mobile/pair/confirm/{id}
  POST /api/mobile/pair/cancel/{id}
  POST /api/mobile/pair/claim          ← 手机无 JWT，靠 pair_id+code
  GET  /api/mobile/pair/devices
  POST /api/mobile/pair/revoke/{id}
  GET  /api/mobile/pair/pending
  GET/POST /api/mobile/mesh
  POST /api/mobile/mesh/auth
  POST /api/mobile/mesh/vps
  POST /api/mobile/mesh/vps/test
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.api.dependencies import get_current_user
from backend.schemas.user import UserRead
from backend.services.mobile_pair_service import get_pair_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mobile", tags=["Mobile Pair"])


class PairStartBody(BaseModel):
    mesh: Optional[str] = None
    require_confirm: Optional[bool] = None
    host: Optional[str] = None
    port: Optional[int] = None
    name: Optional[str] = None


class PairClaimBody(BaseModel):
    pair_id: str
    code: str
    device_name: Optional[str] = None


class PairSessionBody(BaseModel):
    """Phone exchanges one-time claim device token for a real JWT session."""

    token: str = Field(..., min_length=8, description="device token from pair/claim")


class MeshSetBody(BaseModel):
    mode: Optional[str] = None
    require_pair_confirm: Optional[bool] = None
    hostname: Optional[str] = None


class MeshAuthBody(BaseModel):
    auth_key: str = Field(default="", description="Tailscale auth key (tskey-auth-…)")


class MeshVpsBody(BaseModel):
    host: Optional[str] = None
    port: Optional[int] = None
    token: Optional[str] = None
    enabled: Optional[bool] = None
    scheme: Optional[str] = None
    tunnel_id: Optional[str] = None


# ── pair ─────────────────────────────────────────────────────────────────────


@router.post("/pair/start")
async def pair_start(
    body: PairStartBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    """PC 出码：一键生成二维码 URI + 多端点候选。"""
    svc = get_pair_service()
    result = svc.start(
        mesh=body.mesh,
        require_confirm=body.require_confirm,
        host=body.host,
        port=body.port,
        name=body.name,
    )
    logger.info(
        "pair_start user=%s pair_id=%s mesh=%s lan=%s ts=%s",
        getattr(current_user, "email", None),
        result.get("pair_id"),
        result.get("mesh"),
        result.get("lan"),
        result.get("ts"),
    )
    return result


@router.get("/pair/status/{pair_id}")
async def pair_status(
    pair_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    svc = get_pair_service()
    st = svc.status(pair_id)
    if not st:
        raise HTTPException(status_code=404, detail="配对会话不存在或已过期")
    return st


@router.post("/pair/confirm/{pair_id}")
async def pair_confirm(
    pair_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    return get_pair_service().confirm(pair_id)


@router.post("/pair/cancel/{pair_id}")
async def pair_cancel(
    pair_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    return get_pair_service().cancel(pair_id)


@router.post("/pair/claim")
async def pair_claim(body: PairClaimBody) -> dict[str, Any]:
    """手机扫码后调用；无需 JWT。"""
    result = get_pair_service().claim(
        body.pair_id,
        body.code,
        device_name=body.device_name or "Phone",
    )
    if not result.get("ok"):
        # 仍返回 200 + ok:false，便于手机 soft-pair 解析；也兼容 400
        return result
    logger.info("pair_claim ok device=%s", (result.get("device") or {}).get("name"))
    return result


@router.post("/pair/session")
async def pair_session(body: PairSessionBody) -> dict[str, Any]:
    """手机用 claim 下发的 device token 换 JWT。

    为什么需要这个端点：
    - `/auth/auto-login` 仅允许 loopback（single_user_mode 安全闸门）
    - 手机无论走 LAN 还是 VPS，对后端来说都不是「本机」
    - 过去 VPS 路径靠隧道剥 XFF 伪装 127.0.0.1 才能 auto-login，脆弱且易 403
    - claim 已经证明手机持有一次性配对码；device token 是合法的会话凭证
    """
    from backend.api.dependencies import resolve_default_admin_password
    from backend.core.security import create_access_token, get_password_hash
    from backend.core.unit_of_work import UnitOfWork
    from backend.schemas import UserRead

    token = (body.token or "").strip()
    svc = get_pair_service()
    device = svc.validate_token(token)
    if not device:
        raise HTTPException(status_code=401, detail="无效或已撤销的配对令牌")

    try:
        svc.touch_device_token(token)
    except Exception as e:
        logger.debug("pair session last_seen update: %s", e)

    async with UnitOfWork() as uow:
        existing = await uow.users.get_by_email("admin@tevarn.dev")
        if existing:
            user = existing
        else:
            default_pw = resolve_default_admin_password()
            user = await uow.users.create(
                {
                    "email": "admin@tevarn.dev",
                    "username": "admin",
                    "hashed_password": get_password_hash(default_pw),
                    "is_superuser": True,
                    "is_active": True,
                }
            )

    access = create_access_token(
        {"sub": str(user.id)},
        hashed_password=getattr(user, "hashed_password", None),
    )
    logger.info(
        "pair_session ok device=%s user=%s",
        device.get("name"),
        getattr(user, "email", None),
    )
    return {
        "access_token": access,
        "token_type": "bearer",
        "expires_in": 604800,
        "user": UserRead.model_validate(user).model_dump(mode="json"),
        "device": {
            "id": device.get("id"),
            "name": device.get("name"),
            "role": device.get("role"),
        },
        "ok": True,
    }


@router.get("/pair/devices")
async def pair_devices(
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    return {"ok": True, "devices": get_pair_service().list_devices()}


@router.post("/pair/revoke/{device_id}")
async def pair_revoke(
    device_id: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    return get_pair_service().revoke(device_id)


@router.get("/pair/pending")
async def pair_pending(
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    return {"ok": True, "pending": get_pair_service().pending_snapshot()}


# ── mesh ─────────────────────────────────────────────────────────────────────


@router.get("/mesh")
async def mesh_get(
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    return get_pair_service().mesh_status()


@router.post("/mesh")
async def mesh_set(
    body: MeshSetBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    return get_pair_service().set_mesh_config(
        mode=body.mode,
        require_pair_confirm=body.require_pair_confirm,
        hostname=body.hostname,
    )


@router.post("/mesh/auth")
async def mesh_auth(
    body: MeshAuthBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    """一次性粘贴 Tailscale auth key，之后扫码可无感外出连接。"""
    st = get_pair_service().set_auth_key(body.auth_key)
    return {
        "ok": True,
        "auth_key_set": st.get("auth_key_set"),
        "detail": "远程已启用 · 之后扫码即可" if st.get("auth_key_set") else "已清除访问密钥",
        "mesh": st,
    }


@router.get("/mesh/embed")
async def mesh_embed_status(
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    """PC 深度内嵌状态（系统 Tailscale 探测；tsnet 侧车可选后续增强）。"""
    st = get_pair_service().mesh_status()
    return {
        "ok": True,
        "running": bool(st.get("tailscale_ip")),
        "role": "pc",
        "tailscale_ip": st.get("tailscale_ip"),
        "lan_ip": st.get("lan_ip"),
        "auth_key_set": st.get("auth_key_set"),
        "vps": st.get("vps"),
        "detail": st.get("detail"),
    }


@router.post("/mesh/vps")
async def mesh_vps_set(
    body: MeshVpsBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    """保存 VPS 中继配置；enabled=true 时拉起出站隧道。"""
    from backend.services import vps_relay as vps_mod
    from backend.services.vps_tunnel import get_vps_tunnel

    cfg = vps_mod.set_vps_config(
        host=body.host,
        port=body.port,
        token=body.token,
        enabled=body.enabled,
        scheme=body.scheme,
        tunnel_id=body.tunnel_id,
    )
    # restart tunnel according to enabled flag
    try:
        await get_vps_tunnel().restart_if_enabled()
    except Exception as e:
        logger.warning("vps tunnel restart: %s", e)
    # brief wait so status can flip online
    import asyncio

    if cfg.get("enabled"):
        for _ in range(8):
            if get_vps_tunnel().online:
                break
            await asyncio.sleep(0.35)
    st = get_pair_service().mesh_status()
    return {
        "ok": True,
        "vps": st.get("vps"),
        "mesh": st,
        "detail": (st.get("vps") or {}).get("detail") or "已保存",
    }


@router.post("/mesh/vps/test")
async def mesh_vps_test(
    body: MeshVpsBody,
    current_user: Annotated[UserRead, Depends(get_current_user)],
) -> dict[str, Any]:
    """探测 VPS 中继 health + token，不强制启用隧道。"""
    from urllib.parse import urlparse

    from backend.services import vps_relay as vps_mod

    tmp = vps_mod.load_config()
    if body.host is not None:
        h = body.host.strip()
        if "://" in h:
            u = urlparse(h)
            tmp["scheme"] = u.scheme or tmp["scheme"]
            tmp["host"] = u.hostname or ""
            if u.port:
                tmp["port"] = u.port
        elif h.count(":") == 1 and not h.startswith("["):
            hh, pp = h.rsplit(":", 1)
            if pp.isdigit():
                tmp["host"] = hh.strip()
                tmp["port"] = int(pp)
            else:
                tmp["host"] = h
        else:
            tmp["host"] = h
    if body.port is not None:
        tmp["port"] = int(body.port)
    if body.token is not None:
        tmp["master_token"] = body.token.strip()
    if body.scheme is not None and body.scheme.strip():
        tmp["scheme"] = body.scheme.strip().lower()
    return await vps_mod.test_relay(tmp)
