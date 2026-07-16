"""
Cron 路由
定时任务管理 API

新创建的定时任务归属当前用户；user_id 为 None 的数据为全局共享，
所有登录用户均可查看，但只有超级管理员可修改，私有任务仅所有者和超级管理员可操作。
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from backend.repositories import CronJobRepository, WorkflowRepository, AsyncCronExecutionLogRepository
from backend.schemas.cron import CronJobCreate, CronJobRead, CronJobUpdate
from backend.schemas.cron_execution_log import CronExecutionLogRead
from backend.schemas.user import UserRead

from ..dependencies import get_current_user, get_cron_repo, get_cron_execution_log_repo, get_workflow_repo

router = APIRouter(prefix="/cron", tags=["Cron"])


def _is_job_owner_or_admin(job: Any, user: UserRead) -> bool:
    """私有任务仅所有者和超级管理员可操作；全局任务仅超级管理员可修改。"""
    if job.user_id is None:
        return user.is_superuser
    return job.user_id == user.id or user.is_superuser


@router.get("", response_model=list[CronJobRead])
async def list_cron_jobs(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[CronJobRepository, Depends(get_cron_repo)],
):
    """列出当前用户可见的定时任务（含未启用）"""
    return await repo.list_by_user(current_user.id) or []


async def _validate_workflow_id(
    workflow_id: uuid.UUID | None,
    workflow_repo: WorkflowRepository,
    current_user: UserRead,
) -> None:
    if workflow_id is None:
        return
    wf = await workflow_repo.get_by_id(workflow_id)
    if wf is None:
        raise HTTPException(status_code=400, detail="Workflow not found")
    if wf.user_id is not None and wf.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Access denied to workflow")


@router.post("", response_model=CronJobRead)
async def create_cron_job(
    data: CronJobCreate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[CronJobRepository, Depends(get_cron_repo)],
    workflow_repo: Annotated[WorkflowRepository, Depends(get_workflow_repo)],
):
    """创建定时任务"""
    await _validate_workflow_id(data.workflow_id, workflow_repo, current_user)
    payload = data.model_dump()
    payload["user_id"] = current_user.id
    # 预计算 next_run_at
    try:
        from backend.services.cron_scheduler import compute_next_run

        if payload.get("enabled", True):
            payload["next_run_at"] = compute_next_run(payload.get("schedule") or "")
    except Exception:
        pass
    job = await repo.create(payload)
    try:
        from backend.services.cron_scheduler import scheduler

        scheduler.reschedule(job)
    except Exception:
        pass
    return job


@router.get("/{cron_id}", response_model=CronJobRead)
async def get_cron_job(
    cron_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[CronJobRepository, Depends(get_cron_repo)],
):
    """获取定时任务详情"""
    job = await repo.get_by_id(cron_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Cron job not found")
    if not _is_job_owner_or_admin(job, current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    return job


@router.put("/{cron_id}", response_model=CronJobRead)
async def update_cron_job(
    cron_id: uuid.UUID,
    data: CronJobUpdate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[CronJobRepository, Depends(get_cron_repo)],
    workflow_repo: Annotated[WorkflowRepository, Depends(get_workflow_repo)],
):
    """更新定时任务"""
    job = await repo.get_by_id(cron_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Cron job not found")
    if not _is_job_owner_or_admin(job, current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    await _validate_workflow_id(data.workflow_id, workflow_repo, current_user)
    patch = data.model_dump(exclude_unset=True)
    try:
        from backend.services.cron_scheduler import compute_next_run

        sched = patch.get("schedule", job.schedule)
        enabled = patch.get("enabled", job.enabled)
        if enabled and ("schedule" in patch or "enabled" in patch):
            patch["next_run_at"] = compute_next_run(sched)
    except Exception:
        pass
    job = await repo.update(cron_id, patch)
    if job is None:
        raise HTTPException(status_code=404, detail="Cron job not found")
    try:
        from backend.services.cron_scheduler import scheduler

        scheduler.reschedule(job)
    except Exception:
        pass
    return job


@router.get("/{cron_id}/logs", response_model=list[CronExecutionLogRead])
async def list_cron_logs(
    cron_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[CronJobRepository, Depends(get_cron_repo)],
    log_repo: Annotated[AsyncCronExecutionLogRepository, Depends(get_cron_execution_log_repo)],
):
    """列出定时任务最近 50 条执行日志"""
    job = await repo.get_by_id(cron_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Cron job not found")
    if not _is_job_owner_or_admin(job, current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    return await log_repo.list_by_cron_job(cron_id)


@router.delete("/{cron_id}")
async def delete_cron_job(
    cron_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[CronJobRepository, Depends(get_cron_repo)],
):
    """删除定时任务"""
    job = await repo.get_by_id(cron_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Cron job not found")
    if not _is_job_owner_or_admin(job, current_user):
        raise HTTPException(status_code=403, detail="Access denied")
    success = await repo.delete(cron_id)
    if not success:
        raise HTTPException(status_code=404, detail="Cron job not found")
    try:
        from backend.services.cron_scheduler import scheduler

        scheduler._unschedule_job(str(cron_id))
    except Exception:
        pass
    return {"deleted": True}
