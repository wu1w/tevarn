"""
Desktop Agent 路由
桌面自动化 API：截图、键鼠控制、权限管理
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from backend.schemas.user import UserRead
from backend.services.desktop import (
    DesktopAgentService,
    OperationType,
    PermissionLevel,
    get_desktop_service,
)

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
        raise HTTPException(status_code=500, detail=str(e)) from e


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
        raise HTTPException(status_code=500, detail=str(e)) from e


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
        raise HTTPException(status_code=500, detail=str(e)) from e


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
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/permission")
async def clear_permission(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    service: Annotated[DesktopAgentService, Depends(get_service)],
    operation: OperationType | None = None,
    app_name: str | None = None,
):
    """
    清除权限（会话缓存 + 数据库持久化）
    
    如果不指定 operation，则清除所有权限
    """
    try:
        op = operation.value if operation is not None else None
        stats = await service.clear_permissions(
            current_user.id,
            operation=op,
            app_name=app_name,
        )
        return {
            "success": True,
            "message": "权限已清除（会话+数据库）",
            "removed": stats,
        }
        
    except Exception as e:
        logger.error(f"Clear permission failed: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


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


@router.get("/shots/{filename}")
async def get_saved_shot(
    filename: str,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """读取桌面/浏览器截图落盘文件（实时画面面板用）。"""
    import os
    import re
    import tempfile
    from pathlib import Path

    from fastapi.responses import FileResponse

    # 仅允许简单文件名，防路径穿越
    name = Path(filename).name
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,180}", name):
        raise HTTPException(status_code=400, detail="invalid filename")

    shot_dirs = [
        os.environ.get("TAKTON_DESKTOP_SHOT_DIR") or "",
        os.path.join(tempfile.gettempdir(), "takton_desktop_shots"),
        os.path.join(tempfile.gettempdir(), "takton_browser_shots"),
    ]
    for d in shot_dirs:
        if not d:
            continue
        path = Path(d) / name
        try:
            path = path.resolve()
            path.relative_to(Path(d).resolve())
        except Exception:
            continue
        if path.is_file():
            media = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
            return FileResponse(path, media_type=media, filename=name)
    raise HTTPException(status_code=404, detail="shot not found")
