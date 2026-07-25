"""Phase 3 cluster 结果持久化测试

真 sqlite 端到端：cluster_runs 建表 → repo CRUD → 启动清扫。
覆盖：create/finish/get_by_task_id/list_recent/mark_interrupted_running、
status 端点 DB 回落（内存清空后仍可查）。
"""
import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture()
def repo_db(tmp_path):
    """真 sqlite + 全表创建，patch 到 repo 基类的 session 工厂"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/cr.db", future=True)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from backend.models.base import Base
    import backend.models  # noqa: F401 注册全模型

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    with patch("backend.repositories.base.AsyncSessionLocal", SessionLocal):
        yield SessionLocal
    asyncio.run(engine.dispose())


def _create(task_id: str, status: str = "running"):
    from backend.repositories.cluster_run_repo import AsyncClusterRunRepository

    return AsyncClusterRunRepository().create_run({
        "task_id": task_id,
        "plan_id": "plan-1",
        "name": "测试集群",
        "description": "d",
        "status": status,
        "aggregation_strategy": "synthesize",
        "sub_task_count": 2,
        "started_at": datetime.now(timezone.utc),
    })


def test_create_and_get_by_task_id(repo_db):
    async def _run():
        tid = uuid.uuid4().hex
        await _create(tid)
        from backend.repositories.cluster_run_repo import AsyncClusterRunRepository

        row = await AsyncClusterRunRepository().get_by_task_id(tid)
        assert row is not None
        assert row.status == "running"
        assert row.sub_task_count == 2
        assert row.plan_id == "plan-1"

    asyncio.run(_run())


def test_finish_run_updates_result(repo_db):
    async def _run():
        tid = uuid.uuid4().hex
        await _create(tid)
        from backend.repositories.cluster_run_repo import AsyncClusterRunRepository

        repo = AsyncClusterRunRepository()
        await repo.finish_run(
            tid,
            status="completed",
            sub_tasks=[{"id": "task-0", "status": "completed", "metadata": {"review": {"verdict": "pass"}}}],
            aggregated_result={"synthesized": "综合", "rejected": []},
            review={"reviewed": 1, "rejected": 0},
        )
        row = await repo.get_by_task_id(tid)
        assert row.status == "completed"
        assert row.ended_at is not None
        assert row.review == {"reviewed": 1, "rejected": 0}
        assert row.sub_tasks[0]["metadata"]["review"]["verdict"] == "pass"
        # to_status_dict 对齐 status 端点形状
        d = row.to_status_dict()
        assert d["task_id"] == tid and d["aggregated_result"]["synthesized"] == "综合"

    asyncio.run(_run())


def test_list_recent_order(repo_db):
    async def _run():
        from backend.repositories.cluster_run_repo import AsyncClusterRunRepository

        repo = AsyncClusterRunRepository()
        for _ in range(3):
            await _create(uuid.uuid4().hex)
        rows = await repo.list_recent(limit=10)
        assert len(rows) == 3
        # 倒序：最新在前
        assert rows[0].created_at >= rows[-1].created_at

    asyncio.run(_run())


def test_mark_interrupted_running(repo_db):
    """启动清扫：running → interrupted，已完成的不受影响"""
    async def _run():
        from backend.repositories.cluster_run_repo import AsyncClusterRunRepository

        repo = AsyncClusterRunRepository()
        tid_running = uuid.uuid4().hex
        tid_done = uuid.uuid4().hex
        await _create(tid_running)
        await _create(tid_done, status="completed")

        n = await repo.mark_interrupted_running()
        assert n == 1

        r1 = await repo.get_by_task_id(tid_running)
        assert r1.status == "interrupted"
        assert "restarted" in (r1.error or "")
        r2 = await repo.get_by_task_id(tid_done)
        assert r2.status == "completed"

    asyncio.run(_run())


def test_status_endpoint_db_fallback(repo_db):
    """内存清空后，/cluster/status 回落持久化记录（重启场景）"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import cluster as cluster_mod

    tid = uuid.uuid4().hex

    async def _seed():
        await _create(tid)
        from backend.repositories.cluster_run_repo import AsyncClusterRunRepository

        await AsyncClusterRunRepository().finish_run(
            tid,
            status="completed",
            sub_tasks=[{"id": "task-0", "status": "completed"}],
            aggregated_result={"synthesized": "历史综合"},
            review={"reviewed": 1, "rejected": 0},
        )

    asyncio.run(_seed())

    app = FastAPI()
    app.include_router(cluster_mod.router)
    # 内存注册表清空 → 模拟重启后查询
    cluster_mod._active_clusters.clear()
    cluster_mod._running_clusters.clear()

    with TestClient(app) as client:
        resp = client.get(f"/cluster/status/{tid}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "completed"
        assert body["review"] == {"reviewed": 1, "rejected": 0}
        assert body["aggregated_result"]["synthesized"] == "历史综合"

        # 不存在的仍然 404
        assert client.get(f"/cluster/status/{uuid.uuid4().hex}").status_code == 404
