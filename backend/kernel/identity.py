"""0.5 编制与档案：Agent 身份注册表（PLAN_AI_WORKFORCE §3.a/3.c）。

身份与进程严格分离：AgentProcess 可销毁，AgentIdentity 不可销毁
（只能 active/suspended/archived，archived 终态不可逆，审计链不可断）。

红线实现：
- 身份的权限变更必须产生审计事件，禁止静默改权（§3.a）
- Identity Memory 任何修改可追溯到审批人；修改不覆盖，版本链 supersede（§3.c）

所有审计事件经 kernel._emit 进哈希链（process_id 字段用 "identity:<uuid>" 前缀，
与进程事件同链——身份的完整履历就是事件流的一段切片）。
"""

from __future__ import annotations

import logging
import uuid as _uuid
from typing import Any

from sqlalchemy import select

from backend.core.timezone import utc_now

logger = logging.getLogger(__name__)

IDENTITY_STATUSES = ("active", "suspended", "archived")
MEMORY_KINDS = ("persona", "duty", "experience", "preference", "methodology")
MEMORY_SOURCES = ("manual", "distilled", "system")


def _to_uuid(value: Any) -> _uuid.UUID:
    """ORM Uuid(native_uuid=False) 查询需 UUID 对象——str 入参统一转换。"""
    if isinstance(value, _uuid.UUID):
        return value
    return _uuid.UUID(str(value))


class IdentityRegistry:
    """身份注册表。kernel 持有一个实例（可选挂载，None 时身份层关闭）。"""

    def __init__(self, kernel: Any, session_factory: Any) -> None:
        self._kernel = kernel
        self._session_factory = session_factory

    def _emit(self, kind: str, identity_id: Any, detail: dict[str, Any]) -> None:
        self._kernel._emit(kind, f"identity:{identity_id}", detail)

    # ── 身份生命周期 ─────────────────────────────────────────

    async def create(
        self,
        name: str,
        *,
        role: str = "",
        capabilities: list[str] | None = None,
        sub_agent_id: Any = None,
        user_id: Any = None,
        default_token_budget: int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Any:
        from backend.models.agent_identity import AgentIdentity

        name = (name or "").strip()
        if not name:
            raise ValueError("身份名称不能为空")
        async with self._session_factory() as session:
            # 名称唯一（同名档案会让提权/派活解析歧义）
            existing = (
                await session.execute(
                    select(AgentIdentity).where(AgentIdentity.name == name)
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise ValueError(f"身份名称已存在：{name}")
            ident = AgentIdentity(
                name=name,
                role=role,
                capabilities=capabilities,
                sub_agent_id=sub_agent_id,
                user_id=user_id,
                default_token_budget=default_token_budget,
                meta=dict(meta or {}),
            )
            session.add(ident)
            try:
                await session.commit()
            except Exception as e:
                await session.rollback()
                # 并发竞态：DB 层若有 unique 约束也会落到这里
                msg = str(e).lower()
                if "unique" in msg or "duplicate" in msg:
                    raise ValueError(f"身份名称已存在：{name}") from e
                raise
            await session.refresh(ident)
        self._emit("identity_created", ident.id, {
            "name": name, "role": role, "capabilities": capabilities,
        })
        return ident

    async def _transition(self, identity_id: Any, to: str, *, by: str) -> Any:
        from backend.models.agent_identity import AgentIdentity

        identity_id = _to_uuid(identity_id)
        assert to in IDENTITY_STATUSES
        async with self._session_factory() as session:
            ident = (
                await session.execute(
                    select(AgentIdentity).where(AgentIdentity.id == identity_id)
                )
            ).scalar_one_or_none()
            if ident is None:
                raise ValueError(f"未知身份 {identity_id}")
            if ident.status == "archived":
                raise ValueError("身份已归档（终态不可逆）")
            if ident.status == to:
                return ident
            old = ident.status
            ident.status = to
            if to == "archived":
                ident.archived_at = utc_now()
            await session.commit()
            await session.refresh(ident)
        self._emit(
            {"suspended": "identity_suspended", "active": "identity_resumed",
             "archived": "identity_archived"}[to],
            identity_id, {"from": old, "to": to, "by": by},
        )
        return ident

    async def suspend(self, identity_id: Any, *, by: str = "user") -> Any:
        return await self._transition(identity_id, "suspended", by=by)

    async def resume(self, identity_id: Any, *, by: str = "user") -> Any:
        return await self._transition(identity_id, "active", by=by)

    async def archive(self, identity_id: Any, *, by: str = "user") -> Any:
        return await self._transition(identity_id, "archived", by=by)

    async def update_profile(
        self,
        identity_id: Any,
        *,
        name: str | None = None,
        role: str | None = None,
        default_token_budget: int | None = ...,  # type: ignore[assignment]
        by: str = "user",
    ) -> Any:
        """改名 / 职位(role) / 默认预算。全程审计；已归档禁止改。

        default_token_budget: 省略(Ellipsis)=不改；传 None=清空为不限；传 int=设置。
        """
        from backend.models.agent_identity import AgentIdentity

        identity_id = _to_uuid(identity_id)
        new_name = (name or "").strip() if name is not None else None
        new_role = role.strip() if isinstance(role, str) else role

        async with self._session_factory() as session:
            ident = (
                await session.execute(
                    select(AgentIdentity).where(AgentIdentity.id == identity_id)
                )
            ).scalar_one_or_none()
            if ident is None:
                raise ValueError(f"未知身份 {identity_id}")
            if ident.status == "archived":
                raise ValueError("身份已解雇/归档，禁止改档案")

            changes: dict[str, Any] = {}
            if new_name is not None and new_name != ident.name:
                if not new_name:
                    raise ValueError("名称不能为空")
                clash = (
                    await session.execute(
                        select(AgentIdentity).where(
                            AgentIdentity.name == new_name,
                            AgentIdentity.id != identity_id,
                        )
                    )
                ).scalar_one_or_none()
                if clash is not None:
                    raise ValueError(f"名称已被占用：{new_name}")
                changes["name"] = {"from": ident.name, "to": new_name}
                ident.name = new_name

            if new_role is not None and new_role != (ident.role or ""):
                changes["role"] = {"from": ident.role or "", "to": new_role}
                ident.role = new_role

            if default_token_budget is not ...:
                old_b = ident.default_token_budget
                if default_token_budget is None:
                    if old_b is not None:
                        changes["default_token_budget"] = {"from": old_b, "to": None}
                        ident.default_token_budget = None
                else:
                    try:
                        nb = int(default_token_budget)
                    except (TypeError, ValueError) as e:
                        raise ValueError("default_token_budget 须为整数") from e
                    if nb < 0:
                        raise ValueError("default_token_budget 不能为负")
                    if old_b != nb:
                        changes["default_token_budget"] = {"from": old_b, "to": nb}
                        ident.default_token_budget = nb

            if not changes:
                return ident

            await session.commit()
            await session.refresh(ident)

        self._emit("identity_profile_updated", identity_id, {"by": by, "changes": changes})
        return ident

    async def set_capabilities(
        self, identity_id: Any, capabilities: list[str] | None, *, by: str = "user"
    ) -> Any:
        """权限档案变更——必须审计（禁止静默改权）。"""
        from backend.models.agent_identity import AgentIdentity

        identity_id = _to_uuid(identity_id)

        async with self._session_factory() as session:
            ident = (
                await session.execute(
                    select(AgentIdentity).where(AgentIdentity.id == identity_id)
                )
            ).scalar_one_or_none()
            if ident is None:
                raise ValueError(f"未知身份 {identity_id}")
            if ident.status == "archived":
                raise ValueError("身份已归档，禁止改权")
            old = ident.capabilities
            ident.capabilities = capabilities
            await session.commit()
            await session.refresh(ident)
        self._emit("identity_caps_changed", identity_id, {
            "from": old, "to": capabilities, "by": by,
        })
        return ident

    async def get(self, identity_id: Any) -> Any:
        from backend.models.agent_identity import AgentIdentity

        identity_id = _to_uuid(identity_id)

        async with self._session_factory() as session:
            return (
                await session.execute(
                    select(AgentIdentity).where(AgentIdentity.id == identity_id)
                )
            ).scalar_one_or_none()

    async def list(
        self,
        *,
        status: str | None = None,
        user_id: Any | None = None,
        include_orphan: bool = False,
    ) -> list[Any]:
        """列出身份。user_id 非空时按归属过滤（多租户隔离）。

        include_orphan=True：额外包含 user_id IS NULL 的历史行（单用户迁移窗口）。
        """
        from sqlalchemy import or_

        from backend.models.agent_identity import AgentIdentity

        async with self._session_factory() as session:
            q = select(AgentIdentity).order_by(AgentIdentity.created_at)
            if status is not None:
                q = q.where(AgentIdentity.status == status)
            if user_id is not None:
                uid = _to_uuid(user_id)
                if include_orphan:
                    q = q.where(
                        or_(AgentIdentity.user_id == uid, AgentIdentity.user_id.is_(None))
                    )
                else:
                    q = q.where(AgentIdentity.user_id == uid)
            return list((await session.execute(q)).scalars().all())

    # ── Identity Memory（版本链，修改可追溯）────────────────────

    async def add_memory(
        self,
        identity_id: Any,
        kind: str,
        content: str,
        *,
        source: str = "manual",
        approved_by: str | None = None,
    ) -> Any:
        """写入 Identity Memory 条目。distilled 来源必须带 approved_by
        （蒸馏结果走进化审批——PLAN §3.c/3.d 红线）。"""
        from backend.models.agent_identity import IdentityMemoryEntry

        if kind not in MEMORY_KINDS:
            raise ValueError(f"未知记忆类型 {kind}（可选：{MEMORY_KINDS}）")
        if source not in MEMORY_SOURCES:
            raise ValueError(f"未知来源 {source}")
        if source == "distilled" and not approved_by:
            raise ValueError("distilled 记忆必须指定 approved_by（进化审批不可绕过）")
        identity_id = _to_uuid(identity_id)
        async with self._session_factory() as session:
            entry = IdentityMemoryEntry(
                identity_id=identity_id, kind=kind, content=content,
                source=source, approved_by=approved_by,
            )
            session.add(entry)
            await session.commit()
            await session.refresh(entry)
        self._emit("identity_memory_added", identity_id, {
            "entry_id": str(entry.id), "kind": kind, "source": source,
            "approved_by": approved_by,
        })
        await self._index_memory_entry(entry)
        return entry

    async def append_memory(
        self,
        identity_id: Any,
        kind: str,
        content: str,
        *,
        source: str = "manual",
        approved_by: str | None = None,
    ) -> Any:
        """add_memory 别名（seed / 外部脚本兼容）。"""
        return await self.add_memory(
            identity_id, kind, content, source=source, approved_by=approved_by
        )

    async def _index_memory_entry(self, entry: Any) -> None:
        """Identity Memory 入 RAG：向量模式 best-effort；失败入重试队列。

        DB 已是权威源，索引失败不阻塞写入。本地无 RAG 时直接返回。
        每次写入前顺带 flush 一小批 pending，降低「库有、检索没有」窗口。
        """
        try:
            from backend.services.rag.capability import use_vector_rag
            from backend.services.rag.identity_index_queue import (
                enqueue as _enqueue_index,
            )
            from backend.services.rag.identity_index_queue import (
                flush_pending as _flush_index,
            )

            if not use_vector_rag():
                return
            # 补偿此前失败项（限量，不阻塞主路径过久）
            try:
                await _flush_index(limit=5)
            except Exception:
                pass

            from backend.services.rag.factory import RAGServiceFactory

            rag = RAGServiceFactory.get_service()
            ok = await rag.upsert_identity_memory(
                entry_id=str(entry.id),
                identity_id=str(entry.identity_id),
                kind=entry.kind,
                content=entry.content,
                version=int(getattr(entry, "version", 1) or 1),
            )
            if not ok:
                _enqueue_index(
                    entry_id=str(entry.id),
                    identity_id=str(entry.identity_id),
                    kind=str(entry.kind),
                    content=str(entry.content or ""),
                    version=int(getattr(entry, "version", 1) or 1),
                    op="upsert",
                )
        except Exception as e:
            logger.debug("identity memory 向量索引跳过: %s", e)
            try:
                from backend.services.rag.identity_index_queue import (
                    enqueue as _enqueue_index,
                )

                _enqueue_index(
                    entry_id=str(entry.id),
                    identity_id=str(entry.identity_id),
                    kind=str(getattr(entry, "kind", "experience")),
                    content=str(getattr(entry, "content", "") or ""),
                    version=int(getattr(entry, "version", 1) or 1),
                    op="upsert",
                )
            except Exception:
                pass

    async def supersede_memory(
        self, entry_id: Any, new_content: str, *, approved_by: str
    ) -> Any:
        """修改不覆盖：新版本 supersede 旧版本（版本链）。"""
        from backend.models.agent_identity import IdentityMemoryEntry

        if not approved_by:
            raise ValueError("记忆修改必须指定 approved_by（修改可追溯红线）")
        entry_id = _to_uuid(entry_id)
        async with self._session_factory() as session:
            old = (
                await session.execute(
                    select(IdentityMemoryEntry).where(IdentityMemoryEntry.id == entry_id)
                )
            ).scalar_one_or_none()
            if old is None:
                raise ValueError(f"未知记忆条目 {entry_id}")
            if old.superseded_by is not None:
                raise ValueError("该条目已被取代（只能修改当前生效版本）")
            new = IdentityMemoryEntry(
                identity_id=old.identity_id, kind=old.kind, content=new_content,
                source="manual", approved_by=approved_by, version=old.version + 1,
            )
            session.add(new)
            await session.flush()
            old.superseded_by = new.id
            await session.commit()
            await session.refresh(new)
        self._emit("identity_memory_superseded", old.identity_id, {
            "old_entry_id": str(entry_id), "new_entry_id": str(new.id),
            "kind": old.kind, "version": new.version, "approved_by": approved_by,
        })
        # 向量版本链同步：清旧版 + 索引新版；失败入重试队列
        try:
            from backend.services.rag.capability import use_vector_rag

            if use_vector_rag():
                from backend.services.rag.factory import RAGServiceFactory
                from backend.services.rag.identity_index_queue import (
                    enqueue as _enqueue_index,
                )

                rag = RAGServiceFactory.get_service()
                deleted = await rag.delete_identity_memory(str(entry_id))
                if not deleted:
                    _enqueue_index(
                        entry_id=str(entry_id),
                        identity_id=str(old.identity_id),
                        kind=str(old.kind),
                        content="",
                        version=int(old.version or 1),
                        op="delete",
                    )
                await self._index_memory_entry(new)
        except Exception as e:
            logger.debug("identity memory supersede 向量同步跳过: %s", e)
            try:
                from backend.services.rag.identity_index_queue import (
                    enqueue as _enqueue_index,
                )

                _enqueue_index(
                    entry_id=str(entry_id),
                    identity_id=str(old.identity_id),
                    kind=str(old.kind),
                    content="",
                    version=int(getattr(old, "version", 1) or 1),
                    op="delete",
                )
                await self._index_memory_entry(new)
            except Exception:
                pass
        return new

    async def current_memory(self, identity_id: Any, *, kind: str | None = None) -> list[Any]:
        """当前生效版本（superseded_by IS NULL）。"""
        from backend.models.agent_identity import IdentityMemoryEntry

        identity_id = _to_uuid(identity_id)

        async with self._session_factory() as session:
            q = select(IdentityMemoryEntry).where(
                IdentityMemoryEntry.identity_id == identity_id,
                IdentityMemoryEntry.superseded_by.is_(None),
            )
            if kind is not None:
                q = q.where(IdentityMemoryEntry.kind == kind)
            return list((await session.execute(q)).scalars().all())
