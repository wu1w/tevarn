"""
Workflow 路由
工作流管理 API
"""

import logging
import uuid
from typing import Annotated, Any

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.repositories import WorkflowRepository, AsyncWorkflowExecutionRepository
from backend.schemas.user import UserRead
from backend.schemas.workflow import (
    WorkflowCreate,
    WorkflowExecuteRequest,
    WorkflowExecuteResult,
    WorkflowRead,
    WorkflowUpdate,
)
from backend.schemas.workflow_execution import WorkflowExecutionRead
from backend.schemas.workflow_node import get_all_node_type_definitions
from backend.services.workflow_engine import WorkflowEngine, WorkflowExecutionError

from ..dependencies import get_current_user, get_workflow_repo, get_workflow_execution_repo

router = APIRouter(prefix="/workflows", tags=["Workflows"])


@router.get("/node-types")
async def list_node_types(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """获取所有可用的工作流节点类型定义"""
    types = get_all_node_type_definitions()
    return [t.model_dump() for t in types]


@router.get("", response_model=list[WorkflowRead])
async def list_workflows(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[WorkflowRepository, Depends(get_workflow_repo)],
):
    """列出当前用户的所有工作流"""
    return await repo.list_by_user(current_user.id) or []


@router.post("", response_model=WorkflowRead)
async def create_workflow(
    data: WorkflowCreate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[WorkflowRepository, Depends(get_workflow_repo)],
):
    """创建工作流"""
    wf_data = data.model_dump()
    wf_data["user_id"] = current_user.id
    return await repo.create(wf_data)


@router.get("/{workflow_id}", response_model=WorkflowRead)
async def get_workflow(
    workflow_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[WorkflowRepository, Depends(get_workflow_repo)],
):
    """获取工作流详情"""
    wf = await repo.get_by_id(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if getattr(wf, "user_id", None) and wf.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return wf


@router.put("/{workflow_id}", response_model=WorkflowRead)
async def update_workflow(
    workflow_id: uuid.UUID,
    data: WorkflowUpdate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[WorkflowRepository, Depends(get_workflow_repo)],
):
    """更新工作流"""
    wf = await repo.get_by_id(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if getattr(wf, "user_id", None) and wf.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    wf = await repo.update(workflow_id, data.model_dump(exclude_unset=True))
    if wf is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return wf


@router.get("/{workflow_id}/executions", response_model=list[WorkflowExecutionRead])
async def list_workflow_executions(
    workflow_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[WorkflowRepository, Depends(get_workflow_repo)],
    exec_repo: Annotated[AsyncWorkflowExecutionRepository, Depends(get_workflow_execution_repo)],
):
    """列出工作流最近 50 条执行历史"""
    wf = await repo.get_by_id(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if wf.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Access denied")
    return await exec_repo.list_by_workflow(workflow_id)


@router.delete("/{workflow_id}")
async def delete_workflow(
    workflow_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[WorkflowRepository, Depends(get_workflow_repo)],
):
    """删除工作流"""
    wf = await repo.get_by_id(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if getattr(wf, "user_id", None) and wf.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    success = await repo.delete(workflow_id)
    if not success:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"deleted": True}


@router.post("/{workflow_id}/control")
async def control_workflow(
    workflow_id: uuid.UUID,
    action: Annotated[str, Query()],
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[WorkflowRepository, Depends(get_workflow_repo)],
):
    """控制工作流状态：run / pause / resume / stop"""
    wf = await repo.get_by_id(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if getattr(wf, "user_id", None) and wf.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    if action not in ("run", "pause", "resume", "stop"):
        raise HTTPException(status_code=400, detail="Invalid action")

    status_map = {
        "run": "active",
        "pause": "paused",
        "resume": "active",
        "stop": "draft",
    }
    wf = await repo.update_status(workflow_id, status_map[action])
    if wf is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"status": status_map[action]}


@router.post("/{workflow_id}/execute", response_model=WorkflowExecuteResult)
async def execute_workflow(
    workflow_id: uuid.UUID,
    req: WorkflowExecuteRequest,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[WorkflowRepository, Depends(get_workflow_repo)],
):
    """执行工作流"""
    import time

    wf = await repo.get_by_id(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    if getattr(wf, "user_id", None) and wf.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    dag = wf.dag if isinstance(wf.dag, dict) else {}
    if not dag.get("nodes"):
        raise HTTPException(status_code=400, detail="工作流没有定义任何节点")

    engine = WorkflowEngine()
    start = time.time()
    try:
        result = await engine.execute(dag, inputs=req.inputs)
        elapsed = int((time.time() - start) * 1000)
        return WorkflowExecuteResult(
            success=True,
            outputs=result["outputs"],
            logs=result["logs"],
            execution_time_ms=elapsed,
        )
    except WorkflowExecutionError as e:
        elapsed = int((time.time() - start) * 1000)
        return WorkflowExecuteResult(
            success=False,
            outputs={},
            logs=[{"level": "error", "message": str(e)}],
            execution_time_ms=elapsed,
        )
    except Exception as e:
        elapsed = int((time.time() - start) * 1000)
        logger.exception(f"Workflow execution failed: {workflow_id}")
        return WorkflowExecuteResult(
            success=False,
            outputs={},
            logs=[{"level": "error", "message": f"Workflow engine error: {e}"}],
            execution_time_ms=elapsed,
        )
