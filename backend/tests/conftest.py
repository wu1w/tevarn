"""
Test configuration and shared fixtures for backend tests.
"""

import os
import sys
import tempfile
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Ensure backend is importable when running from repo root
repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, repo_root)

# Set strong secrets before importing settings to avoid validation errors
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-do-not-use-in-production")
os.environ.setdefault("API_KEY", "test-api-key-do-not-use-in-production")
# 注意：Settings 的 env_prefix="TAKTON_"——裸 DB_URL 从不生效（历史潜伏 bug，
# 在 kernel routes 引入 DB merge 后暴露为测试间数据污染）。
# 测试库用进程级临时文件而非 :memory:——:memory: 下每个连接是独立库，
# 引擎连接池会让「建表连接」和「查询连接」看到不同的库。
#
# xdist 注意：worker 会继承 controller 的环境。若用 setdefault，所有 gw*
# 会共用 controller 的 TAKTON_DB_URL → 并发 create_all 竞态
# （sqlite3.OperationalError: table … already exists）。
# 必须按 worker+pid 强制隔离。
_XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "") or "main"
_TEST_DB_PATH = os.path.join(
    tempfile.gettempdir(),
    f"takton_test_{_XDIST_WORKER}_{os.getpid()}.db",
)
# Windows 路径用 as_posix，避免反斜杠被 URL 解析吃掉
_TEST_DB_URI = Path(_TEST_DB_PATH).resolve().as_posix()
os.environ["TAKTON_DB_URL"] = f"sqlite+aiosqlite:///{_TEST_DB_URI}"
os.environ.setdefault("SINGLE_USER_MODE", "True")
# 测试模式：禁止 lifespan 拉起 dispatcher/cron/gateway 等常驻后台，
# 否则多次 LifespanManager 启停会在 Windows/CI 上互锁超时。
os.environ.setdefault("TAKTON_TEST_MODE", "1")
os.environ.setdefault("TAKTON_AGENT_DISPATCHER_ENABLED", "false")
os.environ.setdefault("TAKTON_AGENT_KERNEL_PERSISTENCE", "false")

from backend.core.config import Settings
from backend.database import Base, get_db
from backend.main import app
from backend.schemas.user import UserRead

# Use the same per-process temp file DB as the app under test.
TEST_DB_URL = os.environ["TAKTON_DB_URL"]
engine = create_async_engine(TEST_DB_URL, poolclass=NullPool, future=True)
TestingSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


_TABLES_READY = False


def _create_all_safe(sync_conn) -> None:
    """create_all with TOCTOU tolerance (multi-process / fixture+lifespan)."""
    try:
        Base.metadata.create_all(sync_conn, checkfirst=True)
    except OperationalError as e:
        msg = str(getattr(e, "orig", None) or e).lower()
        if "already exists" not in msg:
            raise


@pytest_asyncio.fixture(autouse=True)
async def prepare_test_database() -> AsyncGenerator[None, None]:
    """Ensure schema exists (once per process; file DB survives loop turnover).

    不用 session-scoped async fixture：pytest-asyncio session loop 与
    LifespanManager 在部分平台会互锁超时。表建在磁盘临时库上，create_all
    幂等；进程退出时由 OS 回收临时文件，不再 drop/dispose 触发
    “Event loop is closed”。
    """
    global _TABLES_READY
    if not _TABLES_READY:
        async with engine.begin() as conn:
            await conn.run_sync(_create_all_safe)
        _TABLES_READY = True
    yield


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a fresh database session for a single test."""
    async with TestingSessionLocal() as session:
        yield session
        await session.rollback()


@pytest.fixture
def client(db_session: AsyncSession) -> Generator[TestClient, None, None]:
    """Return a FastAPI TestClient with the test DB session injected."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def settings() -> Settings:
    """Return a Settings instance configured for tests."""
    return Settings(
        jwt_secret=os.environ["JWT_SECRET"],
        api_key=os.environ["API_KEY"],
        db_url=TEST_DB_URL,
        single_user_mode=True,
    )


__all__ = ["client", "db_session", "settings", "UserRead"]
