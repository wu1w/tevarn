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
import time
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

        async with self._session_factory() as session:
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
            await session.commit()
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

    async def list(self, *, status: str | None = None) -> list[Any]:
        from backend.models.agent_identity import AgentIdentity

        async with self._session_factory() as session:
            q = select(AgentIdentity).order_by(AgentIdentity.created_at)
            if status is not None:
                q = q.where(AgentIdentity.status == status)
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
        return entry

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
