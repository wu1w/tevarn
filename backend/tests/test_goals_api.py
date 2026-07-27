"""Goals API 测试：O-KR 树 / CRUD / UUID 转换回归（零 mock，内存 SQLite）

⚠️ 隔离要点：goals 路由的 repo 走全局 `backend.database.AsyncSessionLocal`
（不经过 get_db 依赖注入），conftest 的 override_get_db 对它无效——不隔离
就会写真实库（2026-07-27 实际踩过：takton.db 混入 20 条测试行）。
因此 monkeypatch repo 层 session 工厂到 conftest 的内存引擎。
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import backend.repositories.base as repo_base
from backend.database import Base

# StaticPool + :memory:：单连接共享同一内存库（conftest 的 NullPool 每连接
# 独立空库，repo 路径下会 no such table）。
_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    poolclass=StaticPool,
    connect_args={"check_same_thread": False},
)
_Session = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True, scope="module")
async def _isolate_db():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    orig = repo_base.AsyncSessionLocal
    repo_base.AsyncSessionLocal = _Session
    yield
    repo_base.AsyncSessionLocal = orig
    await _engine.dispose()


def test_create_objective_and_tree(client):
    r = client.post("/api/goals", json={"title": "测试目标", "kind": "objective"})
    assert r.status_code == 200
    o = r.json()
    assert o["kind"] == "objective"
    assert o["status"] == "active"

    tree = client.get("/api/goals/tree").json()
    assert tree["total"] >= 1
    assert any(x["id"] == o["id"] for x in tree["objectives"])


def test_objective_progress_is_kr_mean(client):
    o = client.post("/api/goals", json={"title": "O-mean", "kind": "objective"}).json()
    client.post("/api/goals", json={"title": "KR-40", "kind": "key_result", "parent_id": o["id"], "progress": 40})
    client.post("/api/goals", json={"title": "KR-80", "kind": "key_result", "parent_id": o["id"], "progress": 80})

    tree = client.get("/api/goals/tree").json()
    obj = next(x for x in tree["objectives"] if x["id"] == o["id"])
    assert obj["progress"] == 60.0
    assert len(obj["key_results"]) == 2


def test_update_progress_clamps_and_uuid_regression(client):
    """回归：PUT/DELETE 用 str goal_id 必须正确转 UUID（SQLite hex 无横线）。

    修复前：str 直接 where 匹配失败（静默 0 行 / AttributeError 'str' has no 'hex'）。
    """
    o = client.post("/api/goals", json={"title": "O-uuid", "kind": "objective"}).json()

    r = client.put(f"/api/goals/{o['id']}", json={"progress": 150})
    assert r.status_code == 200
    assert r.json()["progress"] == 100.0  # clamp 上限

    r2 = client.put(f"/api/goals/{o['id']}", json={"progress": -5})
    assert r2.json()["progress"] == 0.0  # clamp 下限

    r3 = client.put(f"/api/goals/{o['id']}", json={"status": "achieved"})
    assert r3.json()["status"] == "achieved"

    # 无效 id → error dict 而非 500
    r4 = client.put("/api/goals/not-a-uuid", json={"progress": 1})
    assert r4.status_code == 200
    assert "error" in r4.json()


def test_delete_goal(client):
    o = client.post("/api/goals", json={"title": "O-del", "kind": "objective"}).json()
    kr = client.post("/api/goals", json={"title": "KR-del", "kind": "key_result", "parent_id": o["id"]}).json()

    r = client.delete(f"/api/goals/{o['id']}")
    assert r.status_code == 200
    assert r.json()["deleted"] is True

    # O 删除后树里不再有它
    tree = client.get("/api/goals/tree").json()
    assert all(x["id"] != o["id"] for x in tree["objectives"])

    # KR 单独删除
    r2 = client.delete(f"/api/goals/{kr['id']}")
    assert r2.json()["deleted"] in (True, False)  # 级联已删则 False，不报错即可
