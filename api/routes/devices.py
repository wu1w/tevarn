"""
Device 路由
设备管理 API
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from backend.repositories import DeviceRepository
from backend.schemas.device import DeviceCreate, DeviceRead, DeviceUpdate
from backend.schemas.user import UserRead

from ..dependencies import get_current_user, get_device_repo

router = APIRouter(prefix="/devices", tags=["Devices"])


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


@router.get("/{device_id}", response_model=DeviceRead)
async def get_device(
    device_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[DeviceRepository, Depends(get_device_repo)],
):
    """获取设备详情"""
    device = await repo.get_by_id(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if getattr(device, "user_id", None) and device.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return device


@router.put("/{device_id}", response_model=DeviceRead)
async def update_device(
    device_id: uuid.UUID,
    data: DeviceUpdate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[DeviceRepository, Depends(get_device_repo)],
):
    """更新设备"""
    device = await repo.get_by_id(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if getattr(device, "user_id", None) and device.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
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
    """删除设备"""
    device = await repo.get_by_id(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if getattr(device, "user_id", None) and device.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
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
    """设备心跳"""
    device = await repo.get_by_id(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if getattr(device, "user_id", None) and device.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    device = await repo.heartbeat(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"ok": True}
