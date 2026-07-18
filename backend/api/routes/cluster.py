"""
Cluster API - 集群模式 API 路由
支持任务分发、并行执行、结果聚合
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from backend.agent.cluster_executor import (
    AggregationStrategy,
    ClusterExecutor,
    ClusterResult,
    TaskStatus,
    get_cluster_executor,
)
from backend.agent.cluster_protocol import (
    ClusterPlan,
    ClusterProtocol,
    TaskCard,
    create_cluster_plan,
    create_task_card,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["cluster"])


# ─────────── 请求/响应模型 ───────────

class SubTaskRequest(BaseModel):
    """子任务请求"""
    name: str
    description: str = ""
    prompt: str
    agent_role: str = "worker"
    agent_config: dict = Field(default_factory=dict)
    priority: str = "normal"
    depends_on: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ClusterExecuteRequest(BaseModel):
    """集群执行请求"""
    task_description: str
    sub_tasks: list[SubTaskRequest]
    max_parallel: int = 5
    timeout_seconds: int = 600
    aggregation_strategy: str = "synthesize"
    context: dict = Field(default_factory=dict)


class TaskDecomposeRequest(BaseModel):
    """任务分解请求"""
    task_description: str
    available_agents: list[dict] | None = None
    max_parallel: int = 5
    timeout_seconds: int = 600
    aggregation_strategy: str = "synthesize"


class ClusterStatusResponse(BaseModel):
    """集群状态响应"""
    task_id: str
    status: str
    progress: int
    sub_tasks: list[dict]
    aggregated_result: Any = None
    error: str | None = None
    started_at: str
    completed_at: str | None = None


# ─────────── 存储（内存，后续可改数据库）───────────

_active_clusters: dict[str, ClusterResult] = {}
_cluster_websockets: dict[str, list[WebSocket]] = {}


# ─────────── API 端点 ───────────

@router.post("/cluster/execute", response_model=dict)
async def execute_cluster(request: ClusterExecuteRequest):
    """
    执行集群任务
    
    请求体：
    ```json
    {
      "task_description": "分析这个项目的代码质量",
      "sub_tasks": [
        {
          "name": "代码结构分析",
          "prompt": "分析项目的代码结构...",
          "agent_role": "specialist",
          "priority": "high"
        },
        {
          "name": "依赖分析",
          "prompt": "分析项目的依赖关系...",
          "agent_role": "worker",
          "depends_on": ["task-1"]
        }
      ],
      "max_parallel": 5,
      "aggregation_strategy": "synthesize"
    }
    ```
    """
    task_id = str(uuid.uuid4())
    
    # 转换请求为执行器格式
    sub_tasks = [
        {
            "id": f"task-{i}",
            "name": st.name,
            "description": st.description,
            "prompt": st.prompt,
            "agent_config": {
                **st.agent_config,
                "role": st.agent_role,
                "priority": st.priority,
            },
            "depends_on": st.depends_on,
            "metadata": {"tags": st.tags},
        }
        for i, st in enumerate(request.sub_tasks)
    ]
    
    # 获取执行器
    executor = get_cluster_executor()
    
    # 执行
    try:
        result = await executor.execute(
            task_description=request.task_description,
            sub_tasks=sub_tasks,
            aggregation_strategy=AggregationStrategy(request.aggregation_strategy),
        )
        
        # 存储结果
        _active_clusters[task_id] = result
        
        return {
            "task_id": task_id,
            "status": result.status.value,
            "sub_tasks": [st.to_dict() for st in result.sub_tasks],
            "aggregated_result": result.aggregated_result,
            "error": result.error,
        }
        
    except Exception as e:
        logger.error(f"Cluster execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cluster/decompose", response_model=dict)
async def decompose_task(request: TaskDecomposeRequest):
    """
    分解任务（协调者 LLM）
    
    使用 LLM 将复杂任务分解为子任务计划
    """
    try:
        from backend.services.llm import LLMServiceFactory
        
        # 创建协调者提示词
        prompt = ClusterProtocol.create_coordinator_prompt(
            task_description=request.task_description,
            available_agents=request.available_agents,
            max_parallel=request.max_parallel,
            timeout_seconds=request.timeout_seconds,
            aggregation_strategy=request.aggregation_strategy,
        )
        
        # 调用 LLM
        llm = LLMServiceFactory.get_service()
        response = await llm.chat_complete([
            {"role": "system", "content": ClusterProtocol.COORDINATOR_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ])
        
        # 解析计划
        response_text = response.content if hasattr(response, 'content') else str(response)
        plan = ClusterProtocol.parse_plan(response_text)
        if plan is None:
            raise HTTPException(status_code=500, detail="Failed to parse task plan")
        
        # 验证计划
        is_valid, errors = ClusterProtocol.validate_plan(plan)
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"Invalid plan: {errors}")
        
        return {
            "plan_id": plan.id,
            "name": plan.name,
            "description": plan.description,
            "tasks": [t.to_dict() for t in plan.tasks],
            "max_parallel": plan.max_parallel,
            "aggregation_strategy": plan.aggregation_strategy,
            "validation": {
                "is_valid": is_valid,
                "errors": errors,
            },
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Task decomposition failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cluster/execute-plan", response_model=dict)
async def execute_plan(plan: ClusterPlan):
    """
    执行预定义的任务计划
    
    直接执行已经分解好的任务计划
    """
    # 验证计划
    is_valid, errors = ClusterProtocol.validate_plan(plan)
    if not is_valid:
        raise HTTPException(status_code=400, detail=f"Invalid plan: {errors}")
    
    # 转换为执行器格式
    sub_tasks = ClusterProtocol.plan_to_executor_format(plan)
    
    # 获取执行器
    executor = get_cluster_executor()
    
    try:
        result = await executor.execute(
            task_description=plan.description,
            sub_tasks=sub_tasks,
            aggregation_strategy=AggregationStrategy(plan.aggregation_strategy),
        )
        
        task_id = str(uuid.uuid4())
        _active_clusters[task_id] = result
        
        return {
            "task_id": task_id,
            "plan_id": plan.id,
            "status": result.status.value,
            "sub_tasks": [st.to_dict() for st in result.sub_tasks],
            "aggregated_result": result.aggregated_result,
            "error": result.error,
        }
        
    except Exception as e:
        logger.error(f"Plan execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cluster/status/{task_id}", response_model=ClusterStatusResponse)
async def get_cluster_status(task_id: str):
    """获取集群任务状态"""
    result = _active_clusters.get(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # 计算进度
    total = len(result.sub_tasks)
    completed = len([t for t in result.sub_tasks if t.status == TaskStatus.COMPLETED])
    progress = int(completed / total * 100) if total > 0 else 0
    
    return ClusterStatusResponse(
        task_id=task_id,
        status=result.status.value,
        progress=progress,
        sub_tasks=[st.to_dict() for st in result.sub_tasks],
        aggregated_result=result.aggregated_result,
        error=result.error,
        started_at=result.started_at.isoformat(),
        completed_at=result.completed_at.isoformat() if result.completed_at else None,
    )


@router.get("/cluster/list", response_model=dict)
async def list_clusters():
    """列出所有集群任务"""
    return {
        "clusters": [
            {
                "task_id": task_id,
                "status": result.status.value,
                "sub_task_count": len(result.sub_tasks),
                "started_at": result.started_at.isoformat(),
            }
            for task_id, result in _active_clusters.items()
        ]
    }


@router.delete("/cluster/{task_id}")
async def cancel_cluster(task_id: str):
    """取消集群任务"""
    result = _active_clusters.get(task_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # TODO: 实现取消逻辑
    result.status = TaskStatus.CANCELLED
    
    return {"task_id": task_id, "status": "cancelled"}


@router.websocket("/cluster/ws/{task_id}")
async def cluster_websocket(websocket: WebSocket, task_id: str):
    """集群任务 WebSocket（实时进度）"""
    await websocket.accept()
    
    if task_id not in _cluster_websockets:
        _cluster_websockets[task_id] = []
    _cluster_websockets[task_id].append(websocket)
    
    try:
        while True:
            # 保持连接，接收客户端消息
            data = await websocket.receive_text()
            
            # 处理心跳
            if data == "ping":
                await websocket.send_text("pong")
            
    except WebSocketDisconnect:
        _cluster_websockets[task_id].remove(websocket)
        if not _cluster_websockets[task_id]:
            del _cluster_websockets[task_id]


# ─────────── 辅助函数 ───────────

async def broadcast_progress(task_id: str, progress: int, message: str):
    """广播进度到 WebSocket"""
    if task_id in _cluster_websockets:
        for ws in _cluster_websockets[task_id]:
            try:
                await ws.send_json({
                    "task_id": task_id,
                    "progress": progress,
                    "message": message,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
            except Exception as e:
                logger.error(f"Failed to broadcast progress: {e}")


# ─────────── 便捷端点 ───────────

@router.post("/cluster/quick", response_model=dict)
async def quick_cluster(
    task_description: str,
    num_agents: int = 3,
    strategy: str = "synthesize",
):
    """
    快速集群（自动生成子任务）
    
    适用于简单场景：自动将任务分解为 N 个子任务并行执行
    """
    # 自动生成子任务
    sub_tasks = [
        SubTaskRequest(
            name=f"子任务 {i+1}",
            description=f"自动分解的子任务 {i+1}",
            prompt=f"{task_description}\n\n这是第 {i+1}/{num_agents} 个子任务，请独立完成。",
            agent_role="worker",
            priority="normal",
        )
        for i in range(num_agents)
    ]
    
    request = ClusterExecuteRequest(
        task_description=task_description,
        sub_tasks=sub_tasks,
        aggregation_strategy=strategy,
    )
    
    return await execute_cluster(request)
