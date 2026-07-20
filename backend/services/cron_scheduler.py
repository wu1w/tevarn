"""Cron 定时任务调度器
基于 asyncio 的内置调度：启动加载 enabled jobs，按表达式/周期触发。

稳健性：
- next_run_at 写入 DB，重启后按 overdue 补跑
- croniter 计算标准 cron 表达式
- 单飞锁：同一 job 不叠跑
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.repositories.cron_repo import AsyncCronJobRepository

logger = logging.getLogger(__name__)


def compute_next_run(schedule: str, now: datetime | None = None) -> datetime | None:
    """根据 schedule 计算下次执行 UTC 时间。"""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    raw = (schedule or "").strip()
    if not raw:
        return None
    schedule_l = raw.lower()

    # every N m/h/d
    if schedule_l.startswith("every "):
        rest = schedule_l[6:].strip()
        parts = rest.split()
        seconds: float | None = None
        if len(parts) >= 2:
            try:
                value = int(parts[0])
                unit = parts[1]
            except ValueError:
                value = 0
                unit = ""
            else:
                if unit.startswith("m"):
                    seconds = value * 60.0
                elif unit.startswith("h"):
                    seconds = value * 3600.0
                elif unit.startswith("d"):
                    seconds = value * 86400.0
        else:
            m = re.match(r"^(\d+)\s*([mhd])", rest)
            if m:
                value = int(m.group(1))
                unit = m.group(2)
                seconds = {"m": 60.0, "h": 3600.0, "d": 86400.0}[unit] * value
        if seconds is None:
            return None
        return now + timedelta(seconds=seconds)

    # ISO timestamp (one-shot)
    try:
        from dateutil import parser

        target = parser.parse(raw)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        return target if target > now else now
    except Exception:
        pass

    # standard cron
    try:
        from croniter import croniter

        base = now
        cron = croniter(raw, base)
        nxt = cron.get_next(datetime)
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=timezone.utc)
        return nxt
    except Exception as e:
        logger.warning("compute_next_run failed for %r: %s", schedule, e)
        return None


class CronScheduler:
    """基于 asyncio 的定时任务调度器"""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._running = False
        self._locks: dict[str, asyncio.Lock] = {}

    async def start(self) -> None:
        if self._running:
            logger.warning("CronScheduler already running")
            return
        self._running = True
        logger.info("CronScheduler starting...")

        try:
            repo = AsyncCronJobRepository()
            jobs = await repo.list_enabled()
            logger.info("Loaded %d enabled cron jobs", len(jobs))
            now = datetime.now(timezone.utc)
            for job in jobs:
                # 补齐 next_run_at；过期则尽快触发（最短 1s）
                nxt = getattr(job, "next_run_at", None)
                if nxt is not None and nxt.tzinfo is None:
                    nxt = nxt.replace(tzinfo=timezone.utc)
                if nxt is None or nxt <= now:
                    overdue = nxt is not None and nxt <= now
                    nxt = compute_next_run(job.schedule, now) or (now + timedelta(seconds=5))
                    if overdue:
                        # 漏跑：1 秒后补一次，再进入正常周期
                        nxt = now + timedelta(seconds=1)
                    try:
                        await repo.update(job.id, {"next_run_at": nxt})
                        job.next_run_at = nxt
                    except Exception as e:
                        logger.warning("Failed to seed next_run_at for %s: %s", job.name, e)
                self._schedule_job(job)
        except Exception as e:
            logger.error("Failed to load cron jobs: %s", e)

    def _schedule_job(self, job: Any) -> None:
        job_id = str(job.id)
        self._unschedule_job(job_id)
        task = asyncio.create_task(self._run_loop(job), name=f"cron:{job.name}")
        self._tasks[job_id] = task
        logger.info("Scheduled cron job '%s' (%s) next=%s", job.name, job.schedule, getattr(job, "next_run_at", None))

    def _unschedule_job(self, job_id: str) -> None:
        if job_id in self._tasks:
            self._tasks[job_id].cancel()
            del self._tasks[job_id]

    def reschedule(self, job: Any) -> None:
        """外部创建/更新 job 后调用。"""
        if not self._running:
            return
        if not getattr(job, "enabled", True):
            self._unschedule_job(str(job.id))
            return
        self._schedule_job(job)

    async def _run_loop(self, job: Any) -> None:
        job_id = str(job.id)
        while self._running:
            try:
                # 每次循环从 DB 刷新 schedule / enabled / next_run_at
                repo = AsyncCronJobRepository()
                fresh = await repo.get_by_id(job.id)
                if fresh is None or not fresh.enabled:
                    logger.info("Cron job %s disabled or deleted, stopping loop", job_id[:8])
                    break
                job = fresh

                now = datetime.now(timezone.utc)
                nxt = job.next_run_at
                if nxt is not None and nxt.tzinfo is None:
                    nxt = nxt.replace(tzinfo=timezone.utc)
                if nxt is None:
                    nxt = compute_next_run(job.schedule, now) or (now + timedelta(seconds=60))
                    await repo.update(job.id, {"next_run_at": nxt})

                delay = (nxt - now).total_seconds()
                if delay > 0:
                    await asyncio.sleep(min(delay, 3600.0))  # 最长睡 1h 再校准
                    # 若未到点（被 1h 切片），继续等
                    now2 = datetime.now(timezone.utc)
                    if nxt > now2:
                        continue

                if not self._running:
                    break

                await self._execute_job(job)

                # 计算下一次
                after = datetime.now(timezone.utc)
                next_time = compute_next_run(job.schedule, after) or (after + timedelta(minutes=5))
                try:
                    await repo.update(job.id, {"next_run_at": next_time})
                    job.next_run_at = next_time
                except Exception as e:
                    logger.warning("Failed to update next_run_at: %s", e)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Cron job '%s' loop error: %s", getattr(job, "name", job_id), e)
                await asyncio.sleep(60)

        self._tasks.pop(job_id, None)

    async def _execute_job(self, job: Any) -> None:
        job_id = job.id
        job_name = job.name
        lock = self._locks.setdefault(str(job_id), asyncio.Lock())
        if lock.locked():
            logger.warning("Cron job '%s' still running, skip this tick", job_name)
            return

        async with lock:
            logger.info("Executing cron job '%s'...", job_name)
            run_status = "success"
            run_error: str | None = None
            output_text: str | None = None
            log_id = None
            log_repo = None

            try:
                try:
                    from backend.repositories.cron_execution_log_repo import (
                        AsyncCronExecutionLogRepository,
                    )

                    log_repo = AsyncCronExecutionLogRepository()
                    if hasattr(log_repo, "start"):
                        log_id = await log_repo.start(job_id)
                except Exception:
                    log_repo = None
                    log_id = None

                if job.workflow_id:
                    from backend.repositories.workflow_repo import WorkflowRepository
                    from backend.services.workflow_engine import WorkflowEngine

                    # WorkflowRepository 可能是 sync 风格包装
                    try:
                        from backend.repositories.workflow_repo import AsyncWorkflowRepository

                        wf_repo = AsyncWorkflowRepository()
                    except Exception:
                        wf_repo = WorkflowRepository()

                    workflow = await wf_repo.get_by_id(job.workflow_id)
                    if not workflow:
                        raise ValueError(f"Workflow {job.workflow_id} not found")

                    dag = workflow.dag if isinstance(workflow.dag, dict) else {}
                    if not dag.get("nodes"):
                        raise ValueError("Workflow DAG has no nodes")

                    engine = WorkflowEngine()
                    result = await engine.execute(
                        dag, workflow_id=str(job.workflow_id), trigger="cron"
                    )
                    if not result.get("success", True):
                        logs = result.get("logs") or []
                        err_msg = next(
                            (
                                log.get("message", "")
                                for log in logs
                                if log.get("level") == "error"
                            ),
                            "Workflow execution failed",
                        )
                        raise RuntimeError(err_msg)
                    logger.info("Cron job '%s' workflow completed", job_name)
                    output_text = str(result.get("output", result))[:2000]
                else:
                    logger.info("Cron job '%s' completed (no workflow)", job_name)
                    output_text = None

            except Exception as e:
                run_status = "failed"
                run_error = str(e)
                logger.error("Cron job '%s' failed: %s", job_name, e)
                output_text = None

            try:
                repo = AsyncCronJobRepository()
                await repo.update_run_status(job_id, run_status, run_error)
                if log_id and log_repo is not None and hasattr(log_repo, "finish"):
                    await log_repo.finish(log_id, run_status, output_text, run_error)
            except Exception as update_err:
                logger.error("Failed to update cron job status: %s", update_err)

            # P1 TEE: cron outcome → evolution assets
            try:
                from backend.evolution.config import get_evolution_config
                from backend.evolution.manager import get_evolution_manager

                if get_evolution_config().enabled and get_evolution_config().from_cron:
                    await get_evolution_manager().run_from_task_outcome(
                        task_name=f"cron:{job_name}",
                        success=(run_status == "success"),
                        detail=(output_text or run_error or "")[:2000],
                        failure_codes=[] if run_status == "success" else ["cron_failed"],
                        session_id=f"cron:{job_id}",
                        source="cron",
                        criteria_summary=f"schedule={getattr(job, 'schedule', '')}",
                    )
            except Exception as evo_err:
                logger.warning("cron evolution hook skipped: %s", evo_err)

    async def stop(self) -> None:
        self._running = False
        for job_id, task in list(self._tasks.items()):
            task.cancel()
        self._tasks.clear()
        logger.info("CronScheduler stopped")

    def _calculate_delay(self, schedule: str, now: datetime) -> float | None:
        """兼容旧测试/调用：返回距下次执行的秒数。"""
        nxt = compute_next_run(schedule, now)
        if nxt is None:
            return None
        return max((nxt - now).total_seconds(), 0.0)


# 全局单例
scheduler = CronScheduler()
