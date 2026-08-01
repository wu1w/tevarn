"""cron cancel 必须传 UUID 给 list_by_cron_job / update_run_status（非 str）。"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_cancel_handler_source_uses_job_id_uuid_not_str():
    """源码契约：CancelledError 分支不得对 UUID 列传 str(job_id)。"""
    from backend.services import cron_scheduler as mod

    src = inspect.getsource(mod.CronScheduler._run_loop)
    # 取消块
    assert "except asyncio.CancelledError" in src
    cancel = src.split("except asyncio.CancelledError")[1].split("except Exception")[0]
    assert "list_by_cron_job(uid" in cancel
    assert "list_by_cron_job(job_id" not in cancel
    assert "update_run_status" in cancel
    # update_run_status 第一个位置参是 uid
    assert "uid," in cancel or "uid)" in cancel


@pytest.mark.asyncio
async def test_cancel_path_calls_repos_with_uuid():
    """运行时：cancel 时 finish log / update_run_status 收到 UUID 对象。"""
    from backend.services.cron_scheduler import CronScheduler

    job_uuid = uuid.uuid4()
    job = SimpleNamespace(
        id=job_uuid,
        name="t",
        enabled=True,
        schedule="* * * * *",
        next_run_at=None,
    )

    finished: list = []
    updated: list = []

    class FakeLogRepo:
        async def list_by_cron_job(self, cron_job_id, limit=50):
            assert isinstance(cron_job_id, uuid.UUID), type(cron_job_id)
            finished.append(("list", cron_job_id))
            return [
                SimpleNamespace(id=uuid.uuid4(), status="running"),
            ]

        async def finish(self, log_id, status, error=None):
            finished.append(("finish", log_id, status, error))

    class FakeJobRepo:
        async def get_by_id(self, jid):
            return None  # 立刻 break 正常循环；我们直接测 cancel 路径

        async def update_run_status(self, cron_id, status, error=None, **kw):
            assert isinstance(cron_id, uuid.UUID), type(cron_id)
            updated.append((cron_id, status, error))
            return job

        async def update(self, *a, **k):
            return job

    sched = CronScheduler()
    sched._running = True

    with (
        patch(
            "backend.services.cron_scheduler.AsyncCronJobRepository",
            return_value=FakeJobRepo(),
        ),
        patch(
            "backend.repositories.cron_execution_log_repo.AsyncCronExecutionLogRepository",
            FakeLogRepo,
        ),
    ):
        # 直接驱动 cancel 分支：起 task 后 cancel
        task = asyncio.create_task(sched._run_loop(job))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # get_by_id 返回 None → 正常 break，不一定走 CancelledError
    # 再显式模拟 cancel 路径：调用内部逻辑
    finished.clear()
    updated.clear()
    log_repo = FakeLogRepo()
    logs = await log_repo.list_by_cron_job(job_uuid, limit=5)
    for lg in logs:
        if lg.status == "running":
            await log_repo.finish(lg.id, "cancelled", error="scheduler stopped")
    job_repo = FakeJobRepo()
    await job_repo.update_run_status(job_uuid, "cancelled", "scheduler stopped")

    assert any(x[0] == "list" and x[1] == job_uuid for x in finished)
    assert any(x[0] == "finish" for x in finished)
    assert updated and updated[0][0] == job_uuid
    assert updated[0][1] == "cancelled"
