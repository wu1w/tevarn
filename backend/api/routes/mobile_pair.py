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


class MeshSetBody(BaseModel):
    mode: Optional[str] = None
    require_pair_confirm: Optional[bool] = None
    hostname: Optional[str] = None


class MeshAuthBody(BaseModel):
    auth_key: str = Field(default="", description="Tailscale auth key (tskey-auth-…)")


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
        "detail": st.get("detail"),
    }
