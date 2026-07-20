"""
Desktop Agent 路由
桌面自动化 API：截图、键鼠控制、权限管理
"""

from __future__ import annotations

import base64
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from backend.services.desktop import (
    DesktopAgentService,
    OperationType,
    PermissionLevel,
    get_desktop_service,
)
from backend.schemas.user import UserRead

from ..dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/desktop", tags=["Desktop"])

_desktop_service = get_desktop_service()


# ────────────────── 请求/响应模型 ──────────────────

class DesktopTaskRequest(BaseModel):
    """桌面任务请求"""
    task: str = Field(..., description="自然语言任务描述")
    permission: PermissionLevel = Field(default=PermissionLevel.ASK, description="权限级别")


class DesktopOperationRequest(BaseModel):
    """桌面操作请求"""
    operation: OperationType
    params: dict[str, Any] = Field(default_factory=dict)
    permission: PermissionLevel = Field(default=PermissionLevel.ASK)


class PermissionRequest(BaseModel):
    """权限设置请求"""
    operation: OperationType
    level: PermissionLevel
    app_name: str | None = None


class DesktopOperationResponse(BaseModel):
    """桌面操作响应"""
    success: bool
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    requires_permission: bool = False


# ────────────────── 依赖注入 ──────────────────

async def get_service() -> DesktopAgentService:
    return _desktop_service


# ────────────────── API 端点 ──────────────────

@router.post("/execute", response_model=DesktopOperationResponse)
async def execute_desktop_task(
    request: DesktopTaskRequest,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    service: Annotated[DesktopAgentService, Depends(get_service)],
):
    """
    执行自然语言桌面任务
    
    Example:
        POST /desktop/execute
        {
            "task": "打开记事本，写一首关于春天的诗",
            "permission": "allow_session"
        }
    """
    try:
        # 初始化服务
        await service.initialize()
        
        # 执行任务
        result = await service.execute_task(
            user_id=current_user.id,
            task=request.task,
            permission=request.permission,
        )
        
        return DesktopOperationResponse(
            success=result.success,
            message=result.message,
            data=result.data,
            error=result.error,
            requires_permission=result.data.get("requires_permission", False),
        )
        
    except Exception as e:
        logger.error(f"Desktop task execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/operation", response_model=DesktopOperationResponse)
async def execute_operation(
    request: DesktopOperationRequest,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    service: Annotated[DesktopAgentService, Depends(get_service)],
):
    """
    执行单个桌面操作
    
    Example:
        POST /desktop/operation
        {
            "operation": "screenshot",
            "params": {},
            "permission": "allow_once"
        }
    """
    try:
        await service.initialize()
        
        result = await service.execute_operation(
            user_id=current_user.id,
            operation=request.operation,
            params=request.params,
            permission=request.permission,
        )
        
        return DesktopOperationResponse(
            success=result.success,
            message=result.message,
            data=result.data,
            error=result.error,
            requires_permission=result.data.get("requires_permission", False),
        )
        
    except Exception as e:
        logger.error(f"Desktop operation failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/screenshot")
async def get_screenshot(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    service: Annotated[DesktopAgentService, Depends(get_service)],
):
    """
    获取当前屏幕截图
    
    Returns:
        {
            "success": true,
            "image": "base64_encoded_image",
            "elements": [...]  // UIA 元素（如可用）
        }
    """
    try:
        await service.initialize()
        
        result = await service.execute_operation(
            user_id=current_user.id,
            operation=OperationType.SCREENSHOT,
            params={},
            permission=PermissionLevel.ASK,
        )
        
        if not result.success:
            raise HTTPException(status_code=403, detail=result.message)
        
        return {
            "success": True,
            "image": result.data.get("image"),
            "elements": result.data.get("elements", []),
            "mode": result.data.get("mode", "mcp"),
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Screenshot failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/permission")
async def set_permission(
    request: PermissionRequest,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    service: Annotated[DesktopAgentService, Depends(get_service)],
):
    """
    设置权限
    
    Example:
        POST /desktop/permission
        {
            "operation": "screenshot",
            "level": "always_allow",
            "app_name": "notepad.exe"
        }
    """
    try:
        await service.set_permission(
            user_id=current_user.id,
            operation=request.operation,
            level=request.level,
            app_name=request.app_name,
        )
        
        return {
            "success": True,
            "message": f"权限已设置: {request.operation} -> {request.level}",
        }
        
    except Exception as e:
        logger.error(f"Set permission failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/permission")
async def clear_permission(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    service: Annotated[DesktopAgentService, Depends(get_service)],
    operation: OperationType | None = None,
    app_name: str | None = None,
):
    """
    清除权限
    
    如果不指定 operation，则清除所有权限
    """
    try:
        service.clear_session_permissions(current_user.id)
        
        # TODO: 清除数据库中的权限
        
        return {
            "success": True,
            "message": "权限已清除",
        }
        
    except Exception as e:
        logger.error(f"Clear permission failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.websocket("/stream")
async def desktop_stream(
    websocket: WebSocket,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    service: Annotated[DesktopAgentService, Depends(get_service)],
):
    """
    实时屏幕流（WebSocket）
    
    用于前端实时预览桌面操作
    """
    await websocket.accept()
    
    try:
        await service.initialize()
        
        stream = await service.get_screen_stream(current_user.id)
        
        async for frame in stream:
            await websocket.send_json(frame)
            
    except WebSocketDisconnect:
        logger.info("Desktop stream disconnected")
    except PermissionError as e:
        await websocket.send_json({
            "type": "error",
            "error": str(e),
        })
        await websocket.close()
    except Exception as e:
        logger.error(f"Desktop stream error: {e}")
        await websocket.send_json({
            "type": "error",
            "error": str(e),
        })
        await websocket.close()
