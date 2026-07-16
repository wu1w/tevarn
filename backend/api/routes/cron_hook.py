"""
CronHook 联动路由
Cron 任务触发后的 Hook 联动：CRUD + 执行日志 + 手动触发
"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.repositories.cron_hook_repo import AsyncCronHookRepository, AsyncCronHookExecutionLogRepository
from backend.repositories.cron_repo import CronJobRepository
from backend.schemas.cron_hook import (
    CronHookCreate,
    CronHookRead,
    CronHookUpdate,
    CronHookExecutionLogRead,
    CronJobWithHooks,
)
from backend.schemas.user import UserRead

from ..dependencies import get_current_user, get_cron_repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cron-hooks", tags=["Cron Hooks"])

_hook_repo = AsyncCronHookRepository()
_execution_log_repo = AsyncCronHookExecutionLogRepository()


async def get_hook_repo() -> AsyncCronHookRepository:
    return _hook_repo


async def get_execution_log_repo() -> AsyncCronHookExecutionLogRepository:
    return _execution_log_repo


@router.get("/cron-job/{cron_job_id}", response_model=list[CronHookRead])
async def list_hooks_for_cron_job(
    cron_job_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    hook_repo: AsyncCronHookRepository = Depends(get_hook_repo),
    cron_repo: CronJobRepository = Depends(get_cron_repo),
):
    """列出某个 CronJob 的所有 Hook"""
    job = await cron_repo.get_by_id(cron_job_id)
    if not job:
        raise HTTPException(status_code=404, detail="CronJob not found")
    return await hook_repo.list_by_cron_job(cron_job_id) or []


@router.get("/cron-job/{cron_job_id}/with-hooks", response_model=CronJobWithHooks)
async def get_cron_job_with_hooks(
    cron_job_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    hook_repo: AsyncCronHookRepository = Depends(get_hook_repo),
    cron_repo: CronJobRepository = Depends(get_cron_repo),
):
    """获取 CronJob 及其关联 Hooks"""
    job = await cron_repo.get_by_id(cron_job_id)
    if not job:
        raise HTTPException(status_code=404, detail="CronJob not found")
    hooks = await hook_repo.list_by_cron_job(cron_job_id) or []
    return CronJobWithHooks(cron_job=job, hooks=hooks)


@router.post("", response_model=CronHookRead, status_code=201)
async def create_hook(
    data: CronHookCreate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    hook_repo: AsyncCronHookRepository = Depends(get_hook_repo),
    cron_repo: CronJobRepository = Depends(get_cron_repo),
):
    """创建 Hook"""
    # 验证 cron_job_id 存在
    job = await cron_repo.get_by_id(data.cron_job_id)
    if not job:
        raise HTTPException(status_code=400, detail="CronJob not found")
    obj = await hook_repo.create({**data.model_dump(), "user_id": current_user.id})
    logger.info(f"CronHook created: {obj.id} ({obj.name}) for cron_job {data.cron_job_id}")
    return obj


@router.get("/{hook_id}", response_model=CronHookRead)
async def get_hook(
    hook_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    hook_repo: AsyncCronHookRepository = Depends(get_hook_repo),
):
    """获取单个 Hook"""
    obj = await hook_repo.get_by_id(hook_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Hook not found")
    return obj


@router.put("/{hook_id}", response_model=CronHookRead)
async def update_hook(
    hook_id: uuid.UUID,
    data: CronHookUpdate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    hook_repo: AsyncCronHookRepository = Depends(get_hook_repo),
):
    """更新 Hook"""
    obj = await hook_repo.get_by_id(hook_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Hook not found")
    if obj.user_id and obj.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Access denied")
    update_data = data.model_dump(exclude_unset=True)
    return await hook_repo.update(hook_id, update_data)


@router.delete("/{hook_id}", status_code=204)
async def delete_hook(
    hook_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    hook_repo: AsyncCronHookRepository = Depends(get_hook_repo),
):
    """删除 Hook"""
    obj = await hook_repo.get_by_id(hook_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Hook not found")
    if obj.user_id and obj.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Access denied")
    await hook_repo.delete(hook_id)
    logger.info(f"CronHook deleted: {hook_id}")


@router.get("/{hook_id}/logs", response_model=list[CronHookExecutionLogRead])
async def get_hook_logs(
    hook_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    hook_repo: AsyncCronHookRepository = Depends(get_hook_repo),
    log_repo: AsyncCronHookExecutionLogRepository = Depends(get_execution_log_repo),
    limit: int = Query(default=50, ge=1, le=200),
):
    """获取 Hook 执行日志"""
    obj = await hook_repo.get_by_id(hook_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Hook not found")
    return await log_repo.list_by_hook(hook_id, limit) or []


@router.post("/{hook_id}/trigger")
async def trigger_hook(
    hook_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    hook_repo: AsyncCronHookRepository = Depends(get_hook_repo),
    log_repo: AsyncCronHookExecutionLogRepository = Depends(get_execution_log_repo),
):
    """手动触发 Hook"""
    import time
    import datetime

    obj = await hook_repo.get_by_id(hook_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Hook not found")

    start = time.time()
    status = "success"
    error_msg = ""

    try:
        if obj.target_type == "workflow":
            # 触发工作流
            from backend.repositories.workflow_repo import WorkflowRepository
            from backend.services.workflow_engine import WorkflowEngine
            wf_repo = WorkflowRepository()
            wf = await wf_repo.get_by_id(obj.target_id)
            if not wf:
                raise Exception(f"Workflow {obj.target_id} not found")
            dag = wf.dag if isinstance(wf.dag, dict) else {}
            engine = WorkflowEngine()
            engine_result = await engine.execute(dag, workflow_id=str(hook.workflow_id), trigger="webhook")
            if not engine_result.get("success", True):
                logs = engine_result.get("logs") or []
                err = next(
                    (log.get("message", "") for log in logs if log.get("level") == "error"),
                    "Workflow execution failed",
                )
                raise Exception(err)
            logger.info(f"Hook triggered workflow: {wf.name}")

        elif obj.target_type == "webhook":
            # 触发 Webhook
            from backend.repositories.webhook_repo import AsyncWebhookRepository
            wh_repo = AsyncWebhookRepository()
            import httpx
            wh = await wh_repo.get_by_id(obj.target_id)
            if not wh:
                raise Exception(f"Webhook {obj.target_id} not found")
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    wh.url,
                    json={"event": "cron_hook", "payload": obj.payload_template or {}},
                    headers=wh.headers or {},
                )
                if resp.status_code >= 300:
                    raise Exception(f"Webhook returned {resp.status_code}")

        elif obj.target_type == "agent":
            # 触发子代理
            from backend.repositories.sub_agent_repo import AsyncSubAgentRepository
            agent_repo = AsyncSubAgentRepository()
            agent = await agent_repo.get_by_id(obj.target_id)
            if not agent:
                raise Exception(f"SubAgent {obj.target_id} not found")
            # TODO: 实际调用子代理执行
            logger.info(f"Hook triggered sub-agent: {agent.name}")

    except Exception as e:
        status = "failed"
        error_msg = str(e)[:500]
        logger.error(f"Hook trigger failed: {e}")

    duration_ms = int((time.time() - start) * 1000)

    # 记录执行日志
    await log_repo.create({
        "hook_id": hook_id,
        "cron_job_id": obj.cron_job_id,
        "event": obj.event,
        "status": status,
        "target_type": obj.target_type,
        "target_id": obj.target_id,
        "error_message": error_msg,
        "duration_ms": duration_ms,
    })

    # 更新 hook 状态
    await hook_repo.update(hook_id, {
        "last_triggered_at": datetime.datetime.now(datetime.timezone.utc),
        "trigger_count": (obj.trigger_count or 0) + 1,
    })

    return {"status": status, "duration_ms": duration_ms, "error": error_msg or None}
