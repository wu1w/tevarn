"""B2 cluster 准入配额测试（零 mock）

真实组件：真实 FastAPI 路由（TestClient）+ 真实 settings set/restore +
真实 _running_clusters 注册表状态 + 真实 tmp sqlite（持久化落库）。
无任何 MagicMock/AsyncMock/替身对象。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.core.config import settings


@pytest.fixture()
def app_client(tmp_path):
    """真实 app + 真实 tmp sqlite（重定向 repo session 工厂到真实库）"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/adm.db", future=True)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from backend.models.base import Base
    import backend.models  # noqa: F401

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())

    import backend.repositories.base as repo_base

    real_factory = repo_base.AsyncSessionLocal
    repo_base.AsyncSessionLocal = SessionLocal

    from backend.api.routes import cluster as cluster_mod

    app = FastAPI()
    app.include_router(cluster_mod.router)
    with TestClient(app) as c:
        yield c, cluster_mod
    cluster_mod._active_clusters.clear()
    cluster_mod._running_clusters.clear()
    repo_base.AsyncSessionLocal = real_factory
    asyncio.run(engine.dispose())


@pytest.fixture()
def tight_quota():
    """真实 settings：配额收紧到 2（set/restore，非 mock）"""
    old = settings.cluster_max_concurrent
    settings.cluster_max_concurrent = 2
    yield 2
    settings.cluster_max_concurrent = old


def _fill_running(cluster_mod, n: int) -> list[str]:
    ids = []
    for _ in range(n):
        tid = uuid.uuid4().hex
        cluster_mod._running_clusters[tid] = {
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        ids.append(tid)
    return ids


def test_admission_429_when_full(app_client, tight_quota):
    """运行数达上限 → /cluster/execute 立即 429（诚实拒绝，不无限排队）"""
    client, cluster_mod = app_client
    _fill_running(cluster_mod, tight_quota)

    resp = client.post("/cluster/execute", json={
        "task_description": "应该被拒绝",
        "sub_tasks": [{"name": "x", "prompt": "p"}],
    })
    assert resp.status_code == 429
    assert "并发已满" in resp.json()["detail"]


def test_admission_passes_after_slot_frees(app_client, tight_quota):
    """腾出槽位后准入恢复（真实后台执行会真实落库 running 记录）"""
    client, cluster_mod = app_client
    ids = _fill_running(cluster_mod, tight_quota)
    cluster_mod._running_clusters.pop(ids[0])  # 模拟一个完成

    resp = client.post("/cluster/execute", json={
        "task_description": "应该准入",
        "sub_tasks": [{"name": "x", "prompt": "p"}],
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "running"
    assert body["task_id"] in cluster_mod._running_clusters

    # 真实后台任务会把 running 记录落库（等其写完）
    import time

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        row = None
        async def _q():
            nonlocal row
            from backend.repositories.cluster_run_repo import AsyncClusterRunRepository
            row = await AsyncClusterRunRepository().get_by_task_id(body["task_id"])
        asyncio.run(_q())
        if row is not None:
            break
        time.sleep(0.05)
    assert row is not None, "后台启动的 cluster 未落库 running 记录"


def test_execute_plan_also_gated(app_client, tight_quota):
    """execute-plan 同样受配额约束"""
    client, cluster_mod = app_client
    _fill_running(cluster_mod, tight_quota)

    resp = client.post("/cluster/execute-plan", json={
        "id": "p1", "name": "n", "description": "d",
        "tasks": [{
            "id": "t0", "name": "x", "description": "",
            "prompt": "p", "agent_role": "worker", "priority": "normal",
            "depends_on": [],
        }],
    })
    assert resp.status_code == 429


def test_default_quota_is_reasonable():
    """默认配额有限且不为 0（防回归成无上限）"""
    assert 1 <= settings.cluster_max_concurrent <= 32
