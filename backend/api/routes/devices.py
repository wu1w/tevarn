"""
Device 路由
设备管理 API + L1 远程 agent 操作
"""

import uuid
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.repositories import DeviceRepository
from backend.schemas.device import DeviceCreate, DeviceRead, DeviceUpdate
from backend.schemas.user import UserRead

from ..dependencies import get_current_user, get_device_repo

router = APIRouter(prefix="/devices", tags=["Devices"])


class DevicePairRequest(BaseModel):
    """登记一台 L1 remote agent。"""

    name: str = Field(..., max_length=128)
    host: str = Field(default="127.0.0.1", max_length=255)
    port: int = Field(default=19876, ge=1, le=65535)
    token: str = Field(..., min_length=4, max_length=256)
    root_hint: Optional[str] = Field(default=None, max_length=512)


class RemoteExecRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=4000)
    cwd: Optional[str] = None


def _ensure_owner(device, user_id: uuid.UUID) -> None:
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if getattr(device, "user_id", None) and device.user_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")


@router.get("", response_model=list[DeviceRead])
async def list_devices(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[DeviceRepository, Depends(get_device_repo)],
):
    """列出当前用户的所有设备"""
    return await repo.list_by_user(current_user.id) or []


@router.post("", response_model=DeviceRead)
async def create_device(
    data: DeviceCreate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[DeviceRepository, Depends(get_device_repo)],
):
    """创建设备（自动关联当前用户）"""
    device_data = data.model_dump()
    device_data["user_id"] = current_user.id
    return await repo.create(device_data)


@router.post("/pair", response_model=DeviceRead)
async def pair_remote_agent(
    data: DevicePairRequest,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[DeviceRepository, Depends(get_device_repo)],
):
    """配对局域网/本机 takton-agent：写入连接信息并尝试 hello+ping。"""
    from backend.services.remote.transport import RemoteAgentError, RemoteTransport

    transport = RemoteTransport(
        url=f"ws://{data.host}:{data.port}",
        token=data.token,
        timeout_s=20.0,
    )
    try:
        hello = await transport.call("hello", {"token": data.token})
        ping = await transport.ping()
    except RemoteAgentError as e:
        raise HTTPException(status_code=400, detail=f"agent unreachable: {e.message}") from e

    caps: list[str] = []
    if isinstance(hello, dict):
        caps = list(hello.get("capabilities") or [])
    latency = ping.get("latency_ms") if isinstance(ping, dict) else None
    config: dict[str, Any] = {
        "agent_host": data.host,
        "agent_port": data.port,
        "agent_token": data.token,
        "agent_url": f"ws://{data.host}:{data.port}",
        "root": (hello or {}).get("root") if isinstance(hello, dict) else data.root_hint,
        "hostname": (hello or {}).get("hostname") if isinstance(hello, dict) else None,
        "platform": (hello or {}).get("platform") if isinstance(hello, dict) else None,
        "last_latency_ms": latency,
        "transport": "l1-ws",
    }
    return await repo.create(
        {
            "name": data.name,
            "device_type": "shell",
            "status": "online",
            "capabilities": caps or ["file.list", "file.read", "exec.run", "ping"],
            "config": config,
            "user_id": current_user.id,
        }
    )



@router.get("/discover")
async def discover_agents(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    timeout_ms: int = 2500,
):
    """mDNS 浏览 `_takton-agent._tcp.local`（无 zeroconf 时返回空列表）。"""
    from backend.services.remote.mdns import browse_agents

    agents = await browse_agents(timeout_ms=min(max(timeout_ms, 500), 8000))
    return {"agents": agents}


@router.get("/{device_id}", response_model=DeviceRead)
async def get_device(
    device_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[DeviceRepository, Depends(get_device_repo)],
):
    device = await repo.get_by_id(device_id)
    _ensure_owner(device, current_user.id)
    return device


@router.put("/{device_id}", response_model=DeviceRead)
async def update_device(
    device_id: uuid.UUID,
    data: DeviceUpdate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[DeviceRepository, Depends(get_device_repo)],
):
    device = await repo.get_by_id(device_id)
    _ensure_owner(device, current_user.id)
    updated = await repo.update(device_id, data.model_dump(exclude_unset=True))
    if updated is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return updated


@router.delete("/{device_id}")
async def delete_device(
    device_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[DeviceRepository, Depends(get_device_repo)],
):
    device = await repo.get_by_id(device_id)
    _ensure_owner(device, current_user.id)
    success = await repo.delete(device_id)
    if not success:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"deleted": True}


@router.post("/{device_id}/heartbeat")
async def device_heartbeat(
    device_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[DeviceRepository, Depends(get_device_repo)],
):
    """设备心跳；若已配对 agent 则额外 remote ping。"""
    device = await repo.get_by_id(device_id)
    _ensure_owner(device, current_user.id)
    device = await repo.heartbeat(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")

    latency_ms = None
    remote_ok = None
    cfg = getattr(device, "config", None) or {}
    if cfg.get("agent_token") or cfg.get("agent_url") or cfg.get("agent_host"):
        from backend.services.remote.transport import (
            RemoteAgentError,
            transport_from_device_config,
        )

        try:
            tr = transport_from_device_config(cfg)
            tr.timeout_s = 10.0
            ping = await tr.ping()
            latency_ms = ping.get("latency_ms")
            remote_ok = True
            new_cfg = {**cfg, "last_latency_ms": latency_ms}
            await repo.update(device_id, {"status": "online", "config": new_cfg})
        except RemoteAgentError as e:
            remote_ok = False
            await repo.update(device_id, {"status": "offline"})
            return {"ok": False, "remote_ok": False, "error": e.message}

    return {"ok": True, "remote_ok": remote_ok, "latency_ms": latency_ms}


@router.post("/{device_id}/remote/ping")
async def remote_ping(
    device_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[DeviceRepository, Depends(get_device_repo)],
):
    device = await repo.get_by_id(device_id)
    _ensure_owner(device, current_user.id)
    from backend.services.remote.transport import RemoteAgentError, transport_from_device_config

    try:
        tr = transport_from_device_config(device.config or {})
        tr.timeout_s = 10.0
        result = await tr.ping()
    except RemoteAgentError as e:
        raise HTTPException(status_code=502, detail=e.message) from e
    cfg = {**(device.config or {}), "last_latency_ms": result.get("latency_ms")}
    await repo.update(device_id, {"status": "online", "config": cfg})
    return result


@router.get("/{device_id}/remote/fs")
async def remote_fs_list(
    device_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[DeviceRepository, Depends(get_device_repo)],
    path: str = ".",
):
    device = await repo.get_by_id(device_id)
    _ensure_owner(device, current_user.id)
    from backend.services.remote.transport import RemoteAgentError, transport_from_device_config

    try:
        return await transport_from_device_config(device.config or {}).call(
            "file.list", {"path": path}
        )
    except RemoteAgentError as e:
        raise HTTPException(status_code=502, detail=e.message) from e


@router.get("/{device_id}/remote/file")
async def remote_file_read(
    device_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[DeviceRepository, Depends(get_device_repo)],
    path: str,
):
    device = await repo.get_by_id(device_id)
    _ensure_owner(device, current_user.id)
    from backend.services.remote.transport import RemoteAgentError, transport_from_device_config

    try:
        return await transport_from_device_config(device.config or {}).call(
            "file.read", {"path": path}
        )
    except RemoteAgentError as e:
        raise HTTPException(status_code=502, detail=e.message) from e


@router.post("/{device_id}/remote/exec")
async def remote_exec(
    device_id: uuid.UUID,
    data: RemoteExecRequest,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[DeviceRepository, Depends(get_device_repo)],
):
    device = await repo.get_by_id(device_id)
    _ensure_owner(device, current_user.id)
    from backend.services.remote.transport import RemoteAgentError, transport_from_device_config

    try:
        tr = transport_from_device_config(device.config or {})
        tr.timeout_s = 45.0
        return await tr.call(
            "exec.run",
            {"command": data.command, "cwd": data.cwd},
        )
    except RemoteAgentError as e:
        raise HTTPException(status_code=502, detail=e.message) from e


async def resolve_device_by_name(repo: DeviceRepository, user_id: uuid.UUID, name: str):
    devices = await repo.list_by_user(user_id) or []
    name_l = name.lower()
    for d in devices:
        if (d.name or "").lower() == name_l:
            return d
    for d in devices:
        if name_l in (d.name or "").lower():
            return d
    return None
