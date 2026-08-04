"""
Cluster API - 集群模式 API 路由
支持任务分发、并行执行、结果聚合
"""

import asyncio
import logging
from collections import OrderedDict  # audit-fix: _active_clusters LRU 需要
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
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
)
from backend.api.dependencies import get_current_user

logger = logging.getLogger(__name__)

# 全部 cluster HTTP 端点统一要求登录。
#
# 此前这个 router 是全项目唯一没有任何鉴权的业务路由（health 除外）：
# POST /api/cluster/execute 接受任意 prompt 并后台跑 LLM，/cluster/list
# 直接吐出全部历史。任何能连到端口的人都能烧光你的 API 额度并读走任务内容。
# 加鉴权对本地使用零成本 —— single_user_mode 下本机请求本来就免 token。
#
# 注意 router 级 dependencies 会同时套到 WebSocket 路由上，而 get_current_user
# 需要 Request，在 WS 上会 TypeError 直接把连接打死。所以 WS 单独放在
# ws_router 里，用自己的鉴权（见 cluster_websocket）。
router = APIRouter(tags=["cluster"], dependencies=[Depends(get_current_user)])
ws_router = APIRouter(tags=["cluster"])


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

# audit-fix: 完成结果表改为容量 64 的 LRU，超出逐出最旧，防内存无界增长
_active_clusters: OrderedDict[str, ClusterResult] = OrderedDict()
_ACTIVE_CLUSTERS_MAX = 64
_cluster_websockets: dict[str, list[WebSocket]] = {}


def _remember_cluster(task_id: str, result: ClusterResult) -> None:
    """audit-fix: 写入 _active_clusters 并做 LRU 逐出。"""
    _active_clusters[task_id] = result
    _active_clusters.move_to_end(task_id)
    while len(_active_clusters) > _ACTIVE_CLUSTERS_MAX:
        _active_clusters.popitem(last=False)


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

    # Phase 2.2：统一 Run 父节点（origin=cluster），与 ClusterRun 互链
    parent_agent_run_id: uuid.UUID | None = None
    try:
        from backend.agent.run_lifecycle import (
            build_create_payload,
            ensure_bookkeeping_session,
        )
        from backend.agent.run_state import RunStatus
        from backend.repositories.agent_run_repo import AsyncAgentRunRepository

        book_sid = await ensure_bookkeeping_session(None, kind="cluster")
        ar_repo = AsyncAgentRunRepository()
        parent_row = await ar_repo.create_run(
            build_create_payload(
                session_id=book_sid,
                mode="cluster",
                origin="cluster",
                input_summary=(name or task_description or "")[:512],
                meta={
                    "cluster_task_id": task_id,
                    "cluster_run_id": task_id,
                    "plan_id": plan_id,
                    "sub_task_count": len(sub_tasks),
                },
                status=RunStatus.EXECUTING.value,
                started_at=datetime.now(timezone.utc),
            )
        )
        parent_agent_run_id = parent_row.id
        # 挂到 executor，供子任务写 parent_run_id
        try:
            executor._unified_parent_run_id = parent_agent_run_id  # type: ignore[attr-defined]
            executor._unified_session_id = book_sid  # type: ignore[attr-defined]
        except Exception:
            pass
    except Exception as e:
        logger.warning("cluster unified parent Run skipped: %s", e)

    try:
        result = await executor.execute(
            task_description=task_description,
            sub_tasks=sub_tasks,
            aggregation_strategy=strategy,
            progress_callback=_on_progress,
        )
        # 若执行中被 cancel，状态可能已是 cancelled
        if _running_clusters.get(task_id, {}).get("cancel_requested"):
            result.status = TaskStatus.CANCELLED
            result.error = result.error or "cancelled by user"
        _remember_cluster(task_id, result)
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
        await _finish_unified_cluster_run(
            parent_agent_run_id,
            status=result.status.value,
            error=result.error,
            summary=str(result.aggregated_result or "")[:500],
            sub_tasks=result.sub_tasks,
        )
        await broadcast_progress(
            task_id, 100, "done", event="completed", status=result.status.value
        )
    except asyncio.CancelledError:
        logger.info("Background cluster %s cancelled", task_id)
        failed = ClusterResult(
            task_id=task_id,
            status=TaskStatus.CANCELLED,
            sub_tasks=[],
            error="cancelled by user",
        )
        failed.completed_at = datetime.now(timezone.utc)
        _remember_cluster(task_id, failed)
        try:
            await repo.finish_run(task_id, status="cancelled", error="cancelled by user")
        except Exception as pe:
            logger.warning("cluster run persist (cancel) skipped: %s", pe)
        await _finish_unified_cluster_run(
            parent_agent_run_id, status="cancelled", error="cancelled by user"
        )
        await broadcast_progress(task_id, 100, "cancelled", event="cancelled", status="cancelled")
        raise
    except Exception as e:
        logger.error(f"Background cluster execution failed: {e}")
        failed = ClusterResult(
            task_id=task_id,
            status=TaskStatus.FAILED,
            sub_tasks=[],
            error=str(e),
        )
        failed.completed_at = datetime.now(timezone.utc)
        _remember_cluster(task_id, failed)
        try:
            await repo.finish_run(task_id, status="failed", error=str(e))
        except Exception as pe:
            logger.warning("cluster run persist (fail) skipped: %s", pe)
        await _finish_unified_cluster_run(
            parent_agent_run_id, status="failed", error=str(e)
        )
        await broadcast_progress(task_id, 100, str(e), event="failed")
    finally:
        _running_clusters.pop(task_id, None)


async def _finish_unified_cluster_run(
    parent_run_id: uuid.UUID | None,
    *,
    status: str,
    error: str | None = None,
    summary: str = "",
    sub_tasks: list | None = None,
) -> None:
    """把 Cluster 结果同步到统一 AgentRun 父/子节点。"""
    if parent_run_id is None:
        return
    try:
        from backend.agent.run_state import RunStatus
        from backend.repositories.agent_run_repo import AsyncAgentRunRepository

        ar = AsyncAgentRunRepository()
        st_map = {
            "completed": RunStatus.DONE.value,
            "failed": RunStatus.FAILED.value,
            "cancelled": RunStatus.CANCELLED.value,
            "running": RunStatus.EXECUTING.value,
        }
        final = st_map.get(str(status).lower(), RunStatus.DONE.value)
        await ar.update_run(
            parent_run_id,
            {
                "status": final,
                "final_summary": (summary or error or "")[:2000],
                "error": (error or None),
                "ended_at": datetime.now(timezone.utc),
            },
        )
        # 子任务各建一条 origin=cluster 子 Run（幂等尽力）
        parent = await ar.get_run(parent_run_id)
        if parent is None or not sub_tasks:
            return
        for st in sub_tasks:
            try:
                st_status = getattr(st, "status", None)
                st_status_v = (
                    st_status.value if hasattr(st_status, "value") else str(st_status or "done")
                )
                child_final = st_map.get(st_status_v.lower(), RunStatus.DONE.value)
                if st_status_v.lower() in ("completed", "success"):
                    child_final = RunStatus.DONE.value
                name = str(getattr(st, "name", "") or getattr(st, "id", "sub"))
                err = getattr(st, "error", None)
                res = getattr(st, "result", None)
                res_s = ""
                if isinstance(res, dict):
                    res_s = str(res.get("result") or res)[:500]
                elif res is not None:
                    res_s = str(res)[:500]
                await ar.create_run(
                    {
                        "session_id": parent.session_id,
                        "user_id": parent.user_id,
                        "status": child_final,
                        "mode": "cluster_sub",
                        "origin": "cluster",
                        "parent_run_id": parent_run_id,
                        "input_summary": name[:512],
                        "final_summary": res_s,
                        "error": str(err) if err else None,
                        "meta": {
                            "cluster_task_id": (parent.meta or {}).get("cluster_task_id"),
                            "sub_task_id": str(getattr(st, "id", "")),
                        },
                        "started_at": getattr(st, "started_at", None),
                        "ended_at": getattr(st, "completed_at", None)
                        or datetime.now(timezone.utc),
                    }
                )
            except Exception as ce:
                logger.debug("cluster child Run skipped: %s", ce)
    except Exception as e:
        logger.warning("unified cluster Run finish skipped: %s", e)


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
        "task": None,  # filled below with asyncio.Task
        "cancel_requested": False,
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
    _running_clusters[task_id]["task"] = bg
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
        raise HTTPException(status_code=500, detail=str(e)) from e


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
    """取消集群任务：取消后台 asyncio.Task，并落库 cancelled。"""
    from backend.repositories.cluster_run_repo import AsyncClusterRunRepository

    running = _running_clusters.get(task_id)
    result = _active_clusters.get(task_id)

    if running is None and result is None:
        # 尝试 DB 中仍 running 的记录
        try:
            repo = AsyncClusterRunRepository()
            row = await repo.get_by_task_id(task_id) if hasattr(repo, "get_by_task_id") else None
            if row is None:
                raise HTTPException(status_code=404, detail="Task not found")
            if getattr(row, "status", None) not in ("running", "pending"):
                return {"task_id": task_id, "status": row.status, "message": "already finished"}
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=404, detail="Task not found") from None

    # 1) 请求取消标志 + cancel 后台 task
    if running is not None:
        running["cancel_requested"] = True
        bg = running.get("task")
        if isinstance(bg, asyncio.Task) and not bg.done():
            bg.cancel()
            try:
                await asyncio.wait_for(bg, timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        _running_clusters.pop(task_id, None)

    # 2) 内存结果标记
    if result is None:
        result = ClusterResult(
            task_id=task_id,
            status=TaskStatus.CANCELLED,
            sub_tasks=[],
            error="cancelled by user",
        )
        result.completed_at = datetime.now(timezone.utc)
        _remember_cluster(task_id, result)
    else:
        result.status = TaskStatus.CANCELLED
        result.error = (result.error or "") + ("; cancelled by user" if result.error else "cancelled by user")
        result.completed_at = datetime.now(timezone.utc)

    # 3) 落库
    try:
        repo = AsyncClusterRunRepository()
        await repo.finish_run(
            task_id,
            status="cancelled",
            error="cancelled by user",
        )
    except Exception as e:
        logger.warning("cluster cancel persist skipped: %s", e)

    try:
        await broadcast_progress(task_id, 100, "cancelled", event="cancelled", status="cancelled")
    except Exception:
        pass

    return {"task_id": task_id, "status": "cancelled"}


@ws_router.websocket("/cluster/ws/{task_id}")
async def cluster_websocket(websocket: WebSocket, task_id: str, token: str = Query("")):
    """集群任务 WebSocket（实时进度）。

    鉴权与 HTTP 侧同口径：有 token 就验 token；无 token 时仅 loopback 放行
    （single_user_mode）。此前完全无鉴权，任何人都能订阅别人的任务进度。
    """
    from backend.api.websocket import _ws_client_is_loopback
    from backend.core.config import settings
    from backend.core.security import decode_access_token

    authorized = False
    if token.strip():
        payload = decode_access_token(token.strip())
        authorized = bool(payload and "sub" in payload)
    elif settings.single_user_mode and _ws_client_is_loopback(websocket):
        authorized = True

    if not authorized:
        await websocket.close(code=1008, reason="Authentication required")
        return

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
        pass
    except Exception as e:
        # audit-fix: 非断连异常也必须走清理，避免注册表泄漏
        logger.debug("cluster ws %s error: %s", task_id, e)
    finally:
        # audit-fix: 统一清理注册表（此前仅 WebSocketDisconnect 分支清理）
        conns = _cluster_websockets.get(task_id)
        if conns is not None:
            try:
                conns.remove(websocket)
            except ValueError:
                pass
            if not conns:
                _cluster_websockets.pop(task_id, None)


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
