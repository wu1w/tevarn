"""
Database connection and session management
SQLAlchemy async engine and session factory
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

from backend.core.config import settings
from backend.models.base import Base

# 导入所有模型以注册到 Base.metadata（必须在 create_all 之前）
import backend.models  # noqa: F401

_is_sqlite = str(settings.db_url).startswith("sqlite")

_engine_kwargs: dict = {
    "echo": False,
    "future": True,
}

if _is_sqlite:
    # 每次操作独立连接，配合单例 repo 的 per-call session，避免共享连接状态。
    # aiosqlite 不需要 check_same_thread；timeout 以秒为单位传给 sqlite3。
    _engine_kwargs["poolclass"] = NullPool
    _engine_kwargs["connect_args"] = {"timeout": 30}

# Create async engine
engine = create_async_engine(settings.db_url, **_engine_kwargs)

if _is_sqlite:
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:
        """WAL + busy_timeout：降低并发读写时的 database is locked。"""
        try:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
        except Exception:
            # 连接事件里绝不能抛错导致 worker 挂掉
            pass


# Create session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncSession:
    """Dependency for getting async database session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """提供事务级 session 上下文，成功自动 commit，异常自动 rollback"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def _migrate_skill_columns(conn) -> None:
    """为旧 skills 表追加自定义 Skill 所需字段（无 Alembic 时的兼容迁移）"""
    columns = [
        ("is_builtin", "BOOLEAN NOT NULL DEFAULT 0"),
        ("handler", "VARCHAR(32) NOT NULL DEFAULT 'http'"),
        ("handler_config", "JSON DEFAULT '{}'"),
    ]
    for col_name, col_def in columns:
        try:
            async with conn.begin_nested():
                await conn.execute(text(f"ALTER TABLE skills ADD COLUMN {col_name} {col_def}"))
        except (OperationalError, ProgrammingError):
            pass


async def _add_column_if_missing(conn, table: str, column: str, col_def: str) -> None:
    """兼容迁移：若列不存在则添加"""
    try:
        async with conn.begin_nested():
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}"))
    except (OperationalError, ProgrammingError):
        pass


async def _migrate_tenant_columns(conn) -> None:
    """为旧表追加租户隔离与审计日志所需字段"""
    await _add_column_if_missing(
        conn, "chunks", "user_id", "CHAR(36)"
    )
    await _add_column_if_missing(
        conn, "ctx_items", "user_id", "CHAR(36)"
    )


async def _migrate_tool_columns(conn) -> None:
    """v3.0: 为旧 tools 表追加统一工具抽象所需字段"""
    await _add_column_if_missing(conn, "tools", "schema", "JSON DEFAULT '{}'")
    await _add_column_if_missing(conn, "tools", "risk_level", "VARCHAR(16) NOT NULL DEFAULT 'medium'")
    await _add_column_if_missing(conn, "tools", "requires_confirmation", "BOOLEAN NOT NULL DEFAULT 0")
    await _add_column_if_missing(conn, "tools", "allowed_paths", "JSON DEFAULT NULL")


async def init_db() -> None:
    """Initialize database tables"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_skill_columns(conn)
        await _migrate_tenant_columns(conn)
        await _migrate_tool_columns(conn)
