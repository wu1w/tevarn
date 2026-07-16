"""
Tool 路由
工具管理
"""

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

from backend.repositories import ToolRepository
from backend.schemas.tool import (
    ToolCreate,
    ToolExecuteRequest,
    ToolExecuteResponse,
    ToolRead,
    ToolToggle,
    ToolUpdate,
)
from backend.schemas.user import UserRead
from backend.services.tools import ToolRegistry

from ..dependencies import get_current_user, get_tool_repo, require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools", tags=["Tools"])


@router.get("", response_model=list[ToolRead])
async def list_tools(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[ToolRepository, Depends(get_tool_repo)],
):
    """列出所有工具（任何登录用户可查看）"""
    tools = await repo.list_all()
    return tools


@router.post("", response_model=ToolRead)
async def create_tool(
    data: ToolCreate,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[ToolRepository, Depends(get_tool_repo)],
):
    """创建新工具（仅管理员）"""
    # 检查名称是否已存在
    existing = await repo.get_tool_by_name(data.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Tool '{data.name}' already exists")

    try:
        tool = await repo.create(data.model_dump())
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail=f"Tool '{data.name}' already exists") from exc
    return tool


@router.get("/{tool_id}", response_model=ToolRead)
async def get_tool(
    tool_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[ToolRepository, Depends(get_tool_repo)],
):
    """获取单个工具详情（任何登录用户可查看）"""
    tool = await repo.get_by_id(tool_id)
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    return tool


@router.put("/{tool_id}", response_model=ToolRead)
async def update_tool(
    tool_id: uuid.UUID,
    data: ToolUpdate,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[ToolRepository, Depends(get_tool_repo)],
):
    """更新工具（仅管理员）"""
    tool = await repo.get_by_id(tool_id)
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")

    if data.name is not None:
        existing = await repo.get_tool_by_name(data.name)
        if existing is not None and existing.id != tool_id:
            raise HTTPException(status_code=409, detail=f"Tool '{data.name}' already exists")

    update_data = data.model_dump(exclude_unset=True)
    try:
        updated = await repo.update(tool_id, update_data)
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail=f"Tool '{data.name}' already exists") from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Tool not found")
    return updated


@router.put("/{tool_id}/toggle", response_model=ToolRead)
async def toggle_tool(
    tool_id: uuid.UUID,
    data: ToolToggle,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[ToolRepository, Depends(get_tool_repo)],
):
    """切换工具启用状态（仅管理员）"""
    tool = await repo.toggle_tool(tool_id, data.enabled)
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    return tool


@router.delete("/{tool_id}")
async def delete_tool(
    tool_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(require_admin)],
    repo: Annotated[ToolRepository, Depends(get_tool_repo)],
):
    """删除工具（仅管理员，内置工具不可删除）"""
    tool = await repo.get_by_id(tool_id)
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    if tool.is_builtin:
        raise HTTPException(status_code=403, detail="Cannot delete built-in tools")

    success = await repo.delete(tool_id)
    if not success:
        raise HTTPException(status_code=404, detail="Tool not found")
    return {"deleted": True}


@router.post("/{tool_id}/execute", response_model=ToolExecuteResponse)
async def execute_tool_endpoint(
    tool_id: uuid.UUID,
    req: ToolExecuteRequest,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[ToolRepository, Depends(get_tool_repo)],
):
    """手动执行工具（调试用，任何登录用户可调用）"""
    tool = await repo.get_by_id(tool_id)
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")

    logger.info(
        f"Tool execution requested: user={current_user.id}, tool={tool.name}, args={req.arguments}"
    )
    try:
        # v3.0: 优先走统一 ToolRegistry，兼容旧版 DB ToolRegistry
        from backend.tools.registry import ToolRegistry as UnifiedToolRegistry

        unified_tool = UnifiedToolRegistry.get(tool.name)
        if unified_tool is not None:
            result = await UnifiedToolRegistry.execute(tool.name, req.arguments)
        else:
            result = await ToolRegistry.execute_tool(tool, req.arguments)
    except Exception as exc:
        logger.exception(f"Tool execution failed: {tool.name}")
        raise HTTPException(status_code=502, detail=f"Tool execution failed: {exc}") from exc
    return ToolExecuteResponse(
        success=not result.startswith("[Error]"),
        result=result,
        tool_name=tool.name,
    )


@router.get("/schema/active", response_model=list[dict[str, Any]])
async def get_active_tool_schemas(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """获取所有启用的工具的 JSON Schema（供 LLM 使用）

    v3.0: 从统一 ToolRegistry 获取，包含 skill、dynamic、builtin 和 mcp 工具。
    """
    from backend.tools.registry import ToolRegistry as UnifiedToolRegistry

    return UnifiedToolRegistry.get_tools_schema()
