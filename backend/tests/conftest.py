"""
Test configuration and shared fixtures for backend tests.
"""

import os
import sys
import tempfile
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
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
_TEST_DB_PATH = os.path.join(tempfile.gettempdir(), f"takton_test_{os.getpid()}.db")
os.environ.setdefault("TAKTON_DB_URL", f"sqlite+aiosqlite:///{_TEST_DB_PATH}")
os.environ.setdefault("SINGLE_USER_MODE", "True")

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


@pytest_asyncio.fixture(scope="session", autouse=True)
async def prepare_test_database() -> AsyncGenerator[None, None]:
    """Create all tables once at the start of the test session."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


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
