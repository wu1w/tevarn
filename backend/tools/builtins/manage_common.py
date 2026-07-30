"""Shared helpers for manage_* tools (Phase 2.4 domain split)."""
from __future__ import annotations

import logging
import uuid as uuid_mod
from typing import Any

logger = logging.getLogger(__name__)


def _parse_uuid(raw: str, field: str = "id") -> uuid_mod.UUID:
    """解析 UUID 字符串，失败抛 ValueError（由调用方转成失败结果）"""
    try:
        return uuid_mod.UUID(str(raw).strip())
    except (ValueError, AttributeError):
        raise ValueError(f"{field} 不是合法 UUID: {raw}") from None


def _iso(v: Any) -> str | None:
    return v.isoformat() if v else None


def _toolsets_to_caps(tools: list[str] | None) -> list[str]:
    """子代理 toolset 名 → 编制 Identity capabilities。"""
    mapping = {
        "file": "file_rw",
        "file_rw": "file_rw",
        "terminal": "command",
        "command": "command",
        "bash": "command",
        "shell": "command",
        "git": "git",
        "web": "web_search",
        "web_search": "web_search",
        "search": "web_search",
        "browser": "browser",
        "calendar": "calendar",
        "notify": "notify",
        "db": "db_read",
        "db_read": "db_read",
    }
    caps: list[str] = []
    for t in tools or []:
        c = mapping.get(str(t).strip().lower())
        if c and c not in caps:
            caps.append(c)
    if not caps:
        caps = ["file_rw", "web_search"]
    return caps


async def _enroll_identity_for_subagent(obj: Any, *, role: str = "") -> tuple[Any | None, str]:
    """SubAgent 创建后同步写入编制 Identity（员工列表真源）。

    返回 (identity|None, note)。名称冲突时尝试挂到已有同名身份。
    """
    try:
        from backend.kernel import get_kernel

        kernel = get_kernel()
        reg = getattr(kernel, "identity_registry", None)
        if reg is None:
            return None, "编制层未启用，仅创建了技能包/子代理"
        name = str(getattr(obj, "name", "") or "").strip()
        if not name:
            return None, "子代理无名称，跳过入编"
        caps = _toolsets_to_caps(getattr(obj, "enabled_toolsets", None))
        role_s = (role or str(getattr(obj, "description", "") or "")).strip() or name
        # 已有同名：挂 sub_agent_id
        existing = None
        try:
            for i in await reg.list(status=None):
                if i.name == name:
                    existing = i
                    break
        except Exception:
            existing = None
        if existing is not None:
            # 尽量补 sub_agent_id / caps
            try:
                async with reg._session_factory() as session:  # type: ignore[attr-defined]
                    from sqlalchemy import select

                    from backend.models.agent_identity import AgentIdentity

                    row = (
                        await session.execute(
                            select(AgentIdentity).where(AgentIdentity.id == existing.id)
                        )
                    ).scalar_one_or_none()
                    if row is not None:
                        if not row.sub_agent_id:
                            row.sub_agent_id = obj.id
                        if not row.role and role_s:
                            row.role = role_s
                        await session.commit()
                        await session.refresh(row)
                        return row, f"已关联已有员工「{name}」"
            except Exception as e:
                logger.warning("link existing identity failed: %s", e)
            return existing, f"员工「{name}」已存在，已关联"
        ident = await reg.create(
            name,
            role=role_s,
            capabilities=caps,
            default_token_budget=100_000,
            sub_agent_id=getattr(obj, "id", None),
            meta={"source": "manage_sub_agent", "skill_pack": "sub_agent"},
        )
        return ident, f"已入编员工「{name}」id={ident.id}"
    except ValueError as e:
        # 名称冲突等
        return None, f"入编跳过: {e}"
    except Exception as e:
        logger.warning("enroll identity for subagent failed: %s", e)
        return None, f"入编失败: {e}"

