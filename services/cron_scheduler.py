"""Cron 定时任务调度器
基于 asyncio 的内置调度，启动时从数据库加载启用的 cron jobs，
按 cron 表达式定时执行 workflow 或 agent。
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from backend.database import AsyncSessionLocal
from backend.repositories.cron_repo import AsyncCronJobRepository

logger = logging.getLogger(__name__)


class CronScheduler:
    """基于 asyncio 的定时任务调度器"""

    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}
        self._running = False

    async def start(self) -> None:
        """启动调度器：加载所有启用的 cron jobs 并调度"""
        if self._running:
            logger.warning("CronScheduler already running")
            return
        self._running = True
        logger.info("CronScheduler starting...")

        try:
            repo = AsyncCronJobRepository()
            jobs = await repo.list_enabled()
            logger.info(f"Loaded {len(jobs)} enabled cron jobs")
            for job in jobs:
                self._schedule_job(job)
        except Exception as e:
            logger.error(f"Failed to load cron jobs: {e}")

    def _schedule_job(self, job: Any) -> None:
        """安排一个 cron job 定时执行"""
        job_id = str(job.id)

        # 取消已有调度
        self._unschedule_job(job_id)

        task = asyncio.create_task(self._run_loop(job))
        self._tasks[job_id] = task
        logger.info(f"Scheduled cron job '{job.name}' ({job.schedule})")

    def _unschedule_job(self, job_id: str) -> None:
        """取消指定 job 的调度"""
        if job_id in self._tasks:
            self._tasks[job_id].cancel()
            del self._tasks[job_id]

    async def _run_loop(self, job: Any) -> None:
        """循环执行 cron job"""
        while self._running:
            try:
                # 计算下次执行的等待时间
                now = datetime.now(timezone.utc)
                delay = self._calculate_delay(job.schedule, now)

                if delay is None:
                    logger.warning(f"Invalid cron schedule '{job.schedule}' for job '{job.name}'")
                    await asyncio.sleep(60)
                    continue

                await asyncio.sleep(delay)

                # 执行 job
                if not self._running:
                    break

                await self._execute_job(job)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cron job '{job.name}' error: {e}")
                await asyncio.sleep(60)

    async def _execute_job(self, job: Any) -> None:
        """执行一个 cron job"""
        job_id = job.id
        job_name = job.name
        logger.info(f"Executing cron job '{job_name}'...")

        run_status = "success"
        run_error: str | None = None

        try:
            if job.workflow_id:
                # 真正加载 workflow 的 DAG 并执行
                from backend.services.workflow_engine import WorkflowEngine
                from backend.repositories.workflow_repo import WorkflowRepository

                wf_repo = WorkflowRepository()
                workflow = await wf_repo.get_by_id(job.workflow_id)
                if not workflow:
                    raise ValueError(f"Workflow {job.workflow_id} not found")

                dag = workflow.dag if isinstance(workflow.dag, dict) else {}
                if not dag.get("nodes"):
                    raise ValueError("Workflow DAG has no nodes")

                engine = WorkflowEngine()
                result = await engine.execute(dag, workflow_id=str(job.workflow_id), trigger="cron")
                if not result.get("success", True):
                    logs = result.get("logs") or []
                    err_msg = next(
                        (log.get("message", "") for log in logs if log.get("level") == "error"),
                        "Workflow execution failed",
                    )
                    raise RuntimeError(err_msg)
                logger.info(f"Cron job '{job_name}' workflow completed")
                output_text = str(result.get("output", result))[:2000]
            else:
                logger.info(f"Cron job '{job_name}' completed (no workflow)")
                output_text = None

        except Exception as e:
            run_status = "failed"
            run_error = str(e)
            logger.error(f"Cron job '{job_name}' failed: {e}")
            output_text = None

        try:
            repo = AsyncCronJobRepository()
            await repo.update_run_status(job_id, run_status, run_error)
            if log_id:
                await log_repo.finish(log_id, run_status, output_text, run_error)
        except Exception as update_err:
            logger.error(f"Failed to update cron job status: {update_err}")

    def _calculate_delay(self, schedule: str, now: datetime) -> float | None:
        """计算到下次执行的时间（秒）
        支持格式：`every N(m|h|d)` 或 ISO 时间戳
        """
        schedule = schedule.strip().lower()

        # every N m/h/d (supports both "every 5m" and "every 5 m")
        if schedule.startswith("every "):
            rest = schedule[6:].strip()
            if not rest:
                return None
            # Split by whitespace first; if that fails, try to parse a compact value+unit.
            parts = rest.split()
            if len(parts) >= 2:
                try:
                    value = int(parts[0])
                    unit = parts[1]
                except ValueError:
                    pass
                else:
                    if unit.startswith("m"):
                        return value * 60.0
                    elif unit.startswith("h"):
                        return value * 3600.0
                    elif unit.startswith("d"):
                        return value * 86400.0
            else:
                # Try compact form like "5m", "1h", "2d"
                import re
                match = re.match(r"^(\d+)\s*([mhd])", rest)
                if match:
                    value = int(match.group(1))
                    unit = match.group(2)
                    if unit == "m":
                        return value * 60.0
                    elif unit == "h":
                        return value * 3600.0
                    elif unit == "d":
                        return value * 86400.0

        # ISO 时间戳：计算到指定时间的秒数
        try:
            from dateutil import parser
            target = parser.parse(schedule)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            delay = (target - now).total_seconds()
            return max(delay, 0)
        except Exception:
            pass

        # cron 表达式：简化处理，每分钟检查一次
        # 完整实现需要 croniter 库
        try:
            import croniter
            cron = croniter.croniter(schedule, now)
            next_time = cron.get_next(datetime)
            return (next_time - now).total_seconds()
        except ImportError:
            pass
        except Exception:
            pass

        return None

    async def stop(self) -> None:
        """停止调度器"""
        self._running = False
        for job_id, task in list(self._tasks.items()):
            task.cancel()
        self._tasks.clear()
        logger.info("CronScheduler stopped")


# 全局单例
scheduler = CronScheduler()
