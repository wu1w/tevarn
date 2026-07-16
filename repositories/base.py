"""
Repository 抽象基类与异步实现辅助类
使用 Repository Pattern 隔离数据库操作，便于后续替换实现
"""

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import AsyncSessionLocal

T = TypeVar("T")


class BaseRepository(ABC, Generic[T]):
    """Repository 抽象基类"""

    @abstractmethod
    async def get_by_id(self, id: Any) -> T | None:
        """根据 ID 获取实体"""
        raise NotImplementedError

    @abstractmethod
    async def get_by_id_for_user(self, id: Any, user_id: Any) -> T | None:
        """根据 ID 获取实体，并校验是否属于指定用户"""
        raise NotImplementedError

    @abstractmethod
    async def create(self, data: dict[str, Any]) -> T:
        """创建实体"""
        raise NotImplementedError

    @abstractmethod
    async def update(self, id: Any, data: dict[str, Any]) -> T | None:
        """更新实体"""
        raise NotImplementedError

    @abstractmethod
    async def delete(self, id: Any) -> bool:
        """删除实体，返回是否成功"""
        raise NotImplementedError


class AsyncBaseRepository(Generic[T]):
    """
    SQLAlchemy AsyncSession 仓库辅助基类。

    统一处理 session 生命周期：
    - 当外部通过 `UnitOfWork` 注入 session 时，仓库只执行 `flush()`，
      事务的 `commit()` / `rollback()` 由 `UnitOfWork` 统一控制，保证原子性。
    - 当仓库自行创建 session 时（如依赖注入中的单例 repo），每次操作创建
      独立 session 并在 finally 中关闭，避免并发请求复用同一（或已关闭的）session。
    """

    def __init__(self, session: AsyncSession | None = None):
        self._session = session
        self._owns_session = session is None

    async def _get_session(self) -> AsyncSession:
        # UnitOfWork 注入的外部 session：始终复用
        if not self._owns_session:
            if self._session is None:
                raise RuntimeError("AsyncBaseRepository expected an injected session")
            return self._session
        # 单例 repo 自有 session：每次操作新建，禁止缓存到 self。
        # 旧实现会把 session 挂在 self._session 上，close 后不清空，
        # 并发请求会拿到已关闭或正在使用的 AsyncSession → 500 INTERNAL_ERROR。
        return AsyncSessionLocal()

    async def _close_session(self, session: AsyncSession) -> None:
        if self._owns_session:
            try:
                await session.close()
            except Exception:
                pass

    async def _maybe_commit(self, session: AsyncSession) -> None:
        """仅当仓库拥有 session 时提交；否则只 flush，由调用方控制事务。"""
        if self._owns_session:
            await session.commit()
        else:
            await session.flush()

    async def _owner_check(self, obj: Any, user_id: Any) -> Any | None:
        """默认对象级归属校验；子类可覆盖或在 SQL 层过滤。"""
        if obj is None:
            return None
        obj_user_id = getattr(obj, "user_id", None)
        if obj_user_id is not None and str(obj_user_id) != str(user_id):
            return None
        return obj

    async def get_by_id_for_user(self, id: Any, user_id: Any) -> T | None:
        """默认实现：先按 ID 查询，再校验 user_id。建议子类在 SQL 层过滤。"""
        obj = await self.get_by_id(id)
        return await self._owner_check(obj, user_id)
