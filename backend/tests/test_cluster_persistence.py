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


# cluster 路由已加鉴权；这些用例不测鉴权，用一个固定假用户绕开
class _FakeUser:
    id = uuid.UUID("00000000-0000-0000-0000-0000000000ff")
    email = "test@tevarn.dev"
    username = "test"
    is_superuser = True
    is_active = True


_FAKE_USER = _FakeUser()


@pytest.fixture()
def repo_db(tmp_path):
    """真 sqlite + 全表创建，patch 到 repo 基类的 session 工厂"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/cr.db", future=True)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    import backend.models  # noqa: F401 注册全模型
    from backend.models.base import Base

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


@pytest.fixture()
def list_client(repo_db):
    """挂真路由的 TestClient；每个用例前后清空 list 历史缓存，避免用例间串扰"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.api.routes import cluster as cluster_mod

    cluster_mod._cluster_history_cache["ts"] = 0.0
    cluster_mod._cluster_history_cache["rows"] = []
    cluster_mod._active_clusters.clear()
    cluster_mod._running_clusters.clear()

    app = FastAPI()
    app.include_router(cluster_mod.router)
    # cluster router 现在要求登录（此前是全项目唯一无鉴权的业务路由）。
    # 本文件测的是历史缓存回落，不是鉴权；而且用例会全局 patch 掉 DB session
    # 来模拟读库失败——那会连带打死 get_current_user 的用户查询。
    # 覆盖依赖，让这些用例专注在它们真正要验证的东西上。
    from backend.api.dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    with TestClient(app) as client:
        yield client, cluster_mod
    app.dependency_overrides.clear()
    cluster_mod._cluster_history_cache["ts"] = 0.0
    cluster_mod._cluster_history_cache["rows"] = []


def test_list_history_ttl_cache(list_client):
    """TTL 内走缓存（新入库不可见）；过期后重新读库可见"""
    client, cluster_mod = list_client
    asyncio.run(_create(uuid.uuid4().hex))
    asyncio.run(_create(uuid.uuid4().hex))

    resp = client.get("/cluster/list")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["clusters"]) == 2

    # TTL 内再入库一条 → 列表仍返回缓存的 2 条
    asyncio.run(_create(uuid.uuid4().hex))
    resp = client.get("/cluster/list")
    assert len(resp.json()["clusters"]) == 2

    # 拨过时钟 → 重新读库 → 3 条
    cluster_mod._cluster_history_cache["ts"] = 0.0
    resp = client.get("/cluster/list")
    assert len(resp.json()["clusters"]) == 3


def test_list_history_db_failure_falls_back_to_cache(list_client):
    """DB 读失败：回落旧缓存（有）或空表（无），HTTP 始终 200"""
    client, cluster_mod = list_client
    asyncio.run(_create(uuid.uuid4().hex))
    resp = client.get("/cluster/list")
    assert resp.status_code == 200
    assert len(resp.json()["clusters"]) == 1

    # 真错误路径：session 工厂指向不存在目录的 sqlite → 连接必失败
    bad_engine = create_async_engine(
        "sqlite+aiosqlite:////nonexistent_dir_b4/bad.db", future=True
    )
    BadSession = async_sessionmaker(bad_engine, class_=AsyncSession, expire_on_commit=False)
    cluster_mod._cluster_history_cache["ts"] = 0.0  # 强制过期，逼出 DB 读
    with patch("backend.repositories.base.AsyncSessionLocal", BadSession):
        resp = client.get("/cluster/list")
        assert resp.status_code == 200, resp.text
        # 回落旧缓存：仍能拿到失败前的那 1 条
        assert len(resp.json()["clusters"]) == 1
    asyncio.run(bad_engine.dispose())


def test_list_cache_returns_copies(list_client):
    """缓存返回拷贝：调用方改写不污染后续读取"""
    client, cluster_mod = list_client
    tid = uuid.uuid4().hex
    asyncio.run(_create(tid))
    resp = client.get("/cluster/list")
    resp.json()["clusters"][0]["status"] = "hacked"

    cluster_mod._cluster_history_cache["ts"] = 0.0  # 过期重读
    resp = client.get("/cluster/list")
    assert resp.json()["clusters"][0]["status"] == "running"
