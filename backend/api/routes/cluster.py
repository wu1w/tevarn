"""
Cluster API - 集群模式 API 路由
支持任务分发、并行执行、结果聚合
"""

import asyncio
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
    review: dict | None = None


# ─────────── 存储（内存，后续可改数据库）───────────

_active_clusters: dict[str, ClusterResult] = {}
_cluster_websockets: dict[str, list[WebSocket]] = {}


# ─────────── API 端点 ───────────

# ─────────── 后台执行注册表 ───────────
# task_id → {"status": "running", "started_at": iso}；完成/失败后移入 _active_clusters
_running_clusters: dict[str, dict] = {}
# 强引用防 GC（create_task 的 fire-and-forget 必须持引用）
_bg_tasks: set[asyncio.Task] = set()

# ─────────── cluster/list 历史短 TTL 缓存（B4）───────────
# 压测病灶：list 每请求一次 per-call session（NullPool 下=新建 sqlite 连接+PRAGMA），
# c=50 并发打到同一端点时连接开销叠加出 0.15% 异常。
# 历史列表允许秒级陈旧（运行中状态由内存注册表覆盖，不受缓存影响）。
_CLUSTER_HISTORY_TTL_S = 1.0
_cluster_history_cache: dict[str, Any] = {"ts": 0.0, "rows": []}


async def _load_cluster_history(limit: int = 50) -> list[dict]:
    """读取持久化 cluster 历史（TTL 缓存；DB 失败回落旧缓存/空表）"""
    import time

    now = time.monotonic()
    if now - _cluster_history_cache["ts"] < _CLUSTER_HISTORY_TTL_S:
        return [dict(r) for r in _cluster_history_cache["rows"]]
    from backend.repositories.cluster_run_repo import AsyncClusterRunRepository

    try:
        rows = [
            {
                "task_id": row.task_id,
                "name": row.name,
                "status": row.status,
                "sub_task_count": row.sub_task_count,
                "started_at": row.started_at.isoformat() if row.started_at else None,
            }
            for row in await AsyncClusterRunRepository().list_recent(limit=limit)
        ]
    except Exception as e:
        logger.warning("cluster list db read failed, memory only: %s", e)
        return [dict(r) for r in _cluster_history_cache["rows"]]
    _cluster_history_cache["ts"] = now
    _cluster_history_cache["rows"] = rows
    return [dict(r) for r in rows]


async def _run_cluster_background(
    task_id: str,
    executor: ClusterExecutor,
    *,
    task_description: str,
    sub_tasks: list[dict],
    strategy: AggregationStrategy,
    plan_id: str | None = None,
    name: str = "",
) -> None:
    """后台执行集群任务：进度经 WS 广播，结果落库 + _active_clusters 供 status 查询"""

    def _on_progress(sub_task_id: str, progress: int, message: str) -> None:
        # executor 以同步方式调用回调 → 调度协程广播
        try:
            asyncio.get_running_loop().create_task(
                broadcast_progress(
                    task_id, progress, message, sub_task_id=sub_task_id
                )
            )
        except RuntimeError:
            pass

    # 持久化：启动即落 running 记录（失败不阻塞执行）
    from backend.repositories.cluster_run_repo import AsyncClusterRunRepository

    repo = AsyncClusterRunRepository()
    try:
        await repo.create_run({
            "task_id": task_id,
            "plan_id": plan_id,
            "name": name or task_description[:60],
            "description": task_description,
            "status": "running",
            "aggregation_strategy": strategy.value,
            "sub_task_count": len(sub_tasks),
            "started_at": datetime.now(timezone.utc),
        })
    except Exception as e:
        logger.warning("cluster run persist (start) skipped: %s", e)

    try:
        result = await executor.execute(
            task_description=task_description,
            sub_tasks=sub_tasks,
            aggregation_strategy=strategy,
            progress_callback=_on_progress,
        )
        _active_clusters[task_id] = result
        try:
            await repo.finish_run(
                task_id,
                status=result.status.value,
                sub_tasks=[st.to_dict() for st in result.sub_tasks],
                aggregated_result=result.aggregated_result
                if isinstance(result.aggregated_result, dict)
                else {"value": result.aggregated_result},
                review=result.metadata.get("review"),
                error=result.error,
            )
        except Exception as e:
            logger.warning("cluster run persist (finish) skipped: %s", e)
        await broadcast_progress(
            task_id, 100, "done", event="completed", status=result.status.value
        )
    except Exception as e:
        logger.error(f"Background cluster execution failed: {e}")
        failed = ClusterResult(
            task_id=task_id,
            status=TaskStatus.FAILED,
            sub_tasks=[],
            error=str(e),
        )
        failed.completed_at = datetime.now(timezone.utc)
        _active_clusters[task_id] = failed
        try:
            await repo.finish_run(task_id, status="failed", error=str(e))
        except Exception as pe:
            logger.warning("cluster run persist (fail) skipped: %s", pe)
        await broadcast_progress(task_id, 100, str(e), event="failed")
    finally:
        _running_clusters.pop(task_id, None)


def _check_cluster_admission() -> None:
    """B2 准入控制：同时运行的 cluster 超限 → 429（诚实拒绝，不无限排队）"""
    from backend.core.config import settings

    limit = int(getattr(settings, "cluster_max_concurrent", 3) or 3)
    running = len(_running_clusters)
    if running >= limit:
        raise HTTPException(
            status_code=429,
            detail=(
                f"cluster 并发已满（{running}/{limit} 运行中）"
                "——稍后重试，或调大 cluster_max_concurrent"
            ),
        )


def _start_cluster_background(
    executor: ClusterExecutor,
    *,
    task_description: str,
    sub_tasks: list[dict],
    strategy: AggregationStrategy,
    plan_id: str | None = None,
    name: str = "",
) -> str:
    """生成 task_id 并启动后台执行，立即返回句柄（先过准入配额）"""
    _check_cluster_admission()
    task_id = str(uuid.uuid4())
    _running_clusters[task_id] = {
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    bg = asyncio.create_task(
        _run_cluster_background(
            task_id,
            executor,
            task_description=task_description,
            sub_tasks=sub_tasks,
            strategy=strategy,
            plan_id=plan_id,
            name=name,
        )
    )
    _bg_tasks.add(bg)
    bg.add_done_callback(_bg_tasks.discard)
    return task_id


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

    # 后台执行，立即返回句柄；进度经 /cluster/ws/{task_id} 推送
    executor = get_cluster_executor()
    handle = _start_cluster_background(
        executor,
        task_description=request.task_description,
        sub_tasks=sub_tasks,
        strategy=AggregationStrategy(request.aggregation_strategy),
    )
    return {
        "task_id": handle,
        "status": "running",
        "ws_url": f"/cluster/ws/{handle}",
    }


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

    # 后台执行，立即返回句柄；进度经 /cluster/ws/{task_id} 推送
    executor = get_cluster_executor()
    handle = _start_cluster_background(
        executor,
        task_description=plan.description,
        sub_tasks=sub_tasks,
        strategy=AggregationStrategy(plan.aggregation_strategy),
        plan_id=plan.id,
        name=plan.name,
    )
    return {
        "task_id": handle,
        "plan_id": plan.id,
        "status": "running",
        "ws_url": f"/cluster/ws/{handle}",
    }


@router.get("/cluster/status/{task_id}", response_model=ClusterStatusResponse)
async def get_cluster_status(task_id: str):
    """获取集群任务状态（运行中 200+running；内存未命中时回落持久化记录）"""
    result = _active_clusters.get(task_id)
    if result is None:
        running = _running_clusters.get(task_id)
        if running is not None:
            return ClusterStatusResponse(
                task_id=task_id,
                status="running",
                progress=0,
                sub_tasks=[],
                started_at=running["started_at"],
            )
        # 持久化回落：重启后历史记录仍可查
        from backend.repositories.cluster_run_repo import AsyncClusterRunRepository

        try:
            row = await AsyncClusterRunRepository().get_by_task_id(task_id)
        except Exception as e:
            logger.warning("cluster status db fallback failed: %s", e)
            row = None
        if row is None:
            raise HTTPException(status_code=404, detail="Task not found")
        total = row.sub_task_count or len(row.sub_tasks or [])
        completed = len([
            t for t in (row.sub_tasks or []) if t.get("status") == "completed"
        ])
        return ClusterStatusResponse(
            task_id=task_id,
            status=row.status,
            progress=int(completed / total * 100) if total else 0,
            sub_tasks=row.sub_tasks or [],
            aggregated_result=row.aggregated_result,
            error=row.error,
            started_at=row.started_at.isoformat() if row.started_at else "",
            completed_at=row.ended_at.isoformat() if row.ended_at else None,
            review=row.review,
        )
    
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
        review=result.metadata.get("review"),
    )


@router.get("/cluster/list", response_model=dict)
async def list_clusters():
    """列出集群任务（内存运行中 + 持久化历史，按时间倒序）"""
    items: dict[str, dict] = {}
    # 持久化历史（含 interrupted/completed/failed，TTL 缓存抗并发）
    for row in await _load_cluster_history(limit=50):
        items[row["task_id"]] = row
    # 内存态覆盖（运行中/刚完成的最新状态优先）
    for task_id, result in _active_clusters.items():
        items[task_id] = {
            "task_id": task_id,
            "name": (result.sub_tasks[0].name if result.sub_tasks else "")[:60],
            "status": result.status.value,
            "sub_task_count": len(result.sub_tasks),
            "started_at": result.started_at.isoformat(),
        }
    for task_id, running in _running_clusters.items():
        items[task_id] = {
            "task_id": task_id,
            "name": "",
            "status": "running",
            "sub_task_count": 0,
            "started_at": running["started_at"],
        }
    clusters = sorted(
        items.values(), key=lambda x: x.get("started_at") or "", reverse=True
    )
    return {"clusters": clusters}


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

async def broadcast_progress(
    task_id: str,
    progress: int,
    message: str,
    *,
    sub_task_id: str | None = None,
    event: str | None = None,
    status: str | None = None,
):
    """广播进度到 WebSocket（event: completed/failed 为终态事件）"""
    if task_id in _cluster_websockets:
        payload: dict[str, Any] = {
            "task_id": task_id,
            "progress": progress,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if sub_task_id:
            payload["sub_task_id"] = sub_task_id
        if event:
            payload["event"] = event
        if status:
            payload["status"] = status
        for ws in _cluster_websockets[task_id]:
            try:
                await ws.send_json(payload)
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
