"""默认编制引导：预置 CEO + 同事模板（幂等）。

- 启动时自动入编 CEO（及默认同事，若编制为空）
- 员工页可「一键起新员工」从 HIRE_TEMPLATES 实例化

用法（仓库根）:
  PYTHONPATH=. python -m backend.scripts.seed_template_crew

API:
  POST /kernel/workforce/seed-template-crew
  GET  /kernel/workforce/hire-templates
  POST /kernel/workforce/hire-from-template
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)

# CEO：始终应在「同时列表」里（用户不用自己建）
CEO_TEMPLATE: dict[str, Any] = {
    "template_id": "ceo",
    "name": "CEO",
    "role": "CEO · 数字管家",
    "icon": "👔",
    "blurb": "拆单派活、汇报进度、协调同事",
    "capabilities": [
        "file_rw",
        "command",
        "web_search",
        "git",
        "browser",
        "notify",
        "crew_steward",
        "use_tool_pack",
        "clarify",
        "manage_goal",
        "manage_mcp",
        "manage_skill",
    ],
    "token_budget": 500_000,
    "persona": "你是用户的数字管家（CEO）。掌控全局，严谨克制，少空话。",
    "duty": "简单问答/检索/读写本会话直接完成；编制派活仅用于并行重活与专职岗位；向主人汇报、协调同事。",
    "is_ceo": True,
    "auto_seed": True,
}

# 干活同事模板：启动时若编制为空会预置一份；也可随时一键再雇（自动避重名）
WORKER_TEMPLATES: list[dict[str, Any]] = [
    {
        "template_id": "researcher",
        "name": "研究员",
        "role": "调研 · 检索综合",
        "icon": "🔍",
        "blurb": "查资料、交叉验证、写结论",
        "capabilities": ["file_rw", "web_search", "browser", "notify"],
        "token_budget": 120_000,
        "persona": "好奇、有据可查，引用可验证来源。",
        "duty": "检索与综合，输出可验证结论与引用。",
        "is_ceo": False,
        "auto_seed": True,
    },
    {
        "template_id": "engineer",
        "name": "工程师",
        "role": "编码 · 调试",
        "icon": "🛠️",
        "blurb": "读改代码、跑测试、修 bug",
        "capabilities": ["file_rw", "command", "web_search", "git", "notify"],
        "token_budget": 150_000,
        "persona": "务实、少空话，先复现再改。",
        "duty": "读代码、改代码、跑测试并汇报 diff 要点。",
        "is_ceo": False,
        "auto_seed": True,
    },
    {
        "template_id": "writer",
        "name": "文秘",
        "role": "写作 · 纪要",
        "icon": "✍️",
        "blurb": "纪要、邮件稿、文档整理",
        "capabilities": ["file_rw", "web_search", "notify"],
        "token_budget": 100_000,
        "persona": "条理清晰，中文表达自然。",
        "duty": "整理纪要、润色文档、起草可直接发送的文案。",
        "is_ceo": False,
        "auto_seed": True,
    },
    {
        "template_id": "ops",
        "name": "运维",
        "role": "运维 · 排障",
        "icon": "⚙️",
        "blurb": "日志、环境、命令排障",
        "capabilities": ["file_rw", "command", "web_search", "notify"],
        "token_budget": 120_000,
        "persona": "冷静、可复现，先看日志再动手。",
        "duty": "排查环境与进程问题，给出可执行的修复步骤。",
        "is_ceo": False,
        "auto_seed": False,  # 不默认入编，仅一键模板
    },
]

# 兼容旧名：TEMPLATES = 启动自动入编集合
TEMPLATES: list[dict[str, Any]] = [CEO_TEMPLATE] + [
    t for t in WORKER_TEMPLATES if t.get("auto_seed")
]

# 一键雇佣目录（含仅模板、不自动入编的岗位）
HIRE_TEMPLATES: list[dict[str, Any]] = [CEO_TEMPLATE] + list(WORKER_TEMPLATES)

# 旧种子名 → 新 canonical（幂等跳过用）
_LEGACY_CEO_NAMES = frozenset({"小白", "管家", "CEO", "ceo"})


def list_hire_templates() -> list[dict[str, Any]]:
    """前端「一键起新员工」目录（不含敏感内部字段之外的全部展示字段）。"""
    out: list[dict[str, Any]] = []
    for t in HIRE_TEMPLATES:
        out.append(
            {
                "template_id": t["template_id"],
                "name": t["name"],
                "role": t["role"],
                "icon": t.get("icon") or "👤",
                "blurb": t.get("blurb") or t.get("duty") or "",
                "capabilities": list(t.get("capabilities") or []),
                "token_budget": int(t.get("token_budget") or 100_000),
                "is_ceo": bool(t.get("is_ceo")),
                "auto_seed": bool(t.get("auto_seed")),
            }
        )
    return out


def _template_by_id(template_id: str) -> dict[str, Any] | None:
    tid = (template_id or "").strip().lower()
    for t in HIRE_TEMPLATES:
        if str(t.get("template_id") or "").lower() == tid:
            return t
    return None


async def _tag_legacy_ceo(registry: Any, ident: Any) -> None:
    """给已有管家补 is_ceo / template_id，不改名。"""
    try:
        meta = dict(getattr(ident, "meta", None) or {})
        if meta.get("is_ceo") and meta.get("template_id") == "ceo":
            return
        from sqlalchemy import select

        from backend.models.agent_identity import AgentIdentity

        async with registry._session_factory() as session:
            row = (
                await session.execute(
                    select(AgentIdentity).where(AgentIdentity.id == ident.id)
                )
            ).scalar_one_or_none()
            if row is None:
                return
            m = dict(row.meta or {})
            if m.get("is_ceo") and m.get("template_id") == "ceo":
                return
            m["is_ceo"] = True
            m.setdefault("template_id", "ceo")
            m.setdefault("icon", "👔")
            row.meta = m
            await session.commit()
            # 刷新缓存
            try:
                await session.refresh(row)
                registry._push_identity_cache(row)
            except Exception:
                pass
    except Exception as e:
        logger.debug("tag legacy ceo skip: %s", e)


async def _unique_name(registry: Any, base: str) -> str:
    """名称冲突时加数字后缀。"""
    base = (base or "员工").strip() or "员工"
    existing = {i.name for i in await registry.list(status=None)}
    if base not in existing:
        return base
    for n in range(2, 100):
        cand = f"{base}{n}"
        if cand not in existing:
            return cand
    return f"{base}-{os.getpid()}"


async def _hire_one(
    registry: Any,
    t: dict[str, Any],
    *,
    user_id: Any = None,
    name_override: str | None = None,
    allow_rename: bool = False,
) -> dict[str, Any]:
    """创建一名员工。allow_rename=False 时重名跳过；True 时自动改名。"""
    desired = (name_override or t["name"]).strip()
    existing_names = {i.name for i in await registry.list(status=None)}
    if desired in existing_names:
        if not allow_rename:
            return {"status": "skipped", "name": desired, "reason": "exists"}
        desired = await _unique_name(registry, desired)

    meta = {
        "source": "template_seed",
        "template_id": t.get("template_id"),
        "is_ceo": bool(t.get("is_ceo")),
        "icon": t.get("icon") or "👤",
        "blurb": t.get("blurb") or "",
    }
    ident = await registry.create(
        desired,
        role=str(t.get("role") or ""),
        capabilities=list(t.get("capabilities") or []),
        default_token_budget=int(t.get("token_budget") or 100_000),
        user_id=user_id,
        meta=meta,
    )
    for kind, key in (("persona", "persona"), ("duty", "duty")):
        content = str(t.get(key) or "").strip()
        if not content:
            continue
        try:
            await registry.append_memory(
                ident.id, kind, content, source="system", approved_by="seed"
            )
        except Exception as e:
            logger.debug("seed memory %s skip: %s", kind, e)
    return {
        "status": "created",
        "name": desired,
        "id": str(ident.id),
        "role": str(t.get("role") or ""),
        "template_id": t.get("template_id"),
        "is_ceo": bool(t.get("is_ceo")),
    }


_CEO_DUTY_MARKER = "简单问答/检索/读写本会话直接完成"


async def soft_update_ceo_duty(registry: Any) -> list[str]:
    """Refresh duty on existing CEO identities that still have the old dispatch-first text.

    Uses supersede_memory so the version chain stays auditable. Idempotent when
    current duty already contains the solo-default marker.
    """
    updated: list[str] = []
    try:
        items = await registry.list(status=None)
    except Exception as e:
        logger.debug("soft_update_ceo_duty list skip: %s", e)
        return updated
    new_duty = str(CEO_TEMPLATE.get("duty") or "").strip()
    if not new_duty or _CEO_DUTY_MARKER not in new_duty:
        return updated
    for ident in items:
        try:
            meta = getattr(ident, "meta", None) or {}
            role = str(getattr(ident, "role", "") or "").lower()
            name = str(getattr(ident, "name", "") or "")
            is_ceo = False
            if isinstance(meta, dict) and (
                meta.get("is_ceo") or meta.get("template_id") == "ceo"
            ):
                is_ceo = True
            if name in _LEGACY_CEO_NAMES or "ceo" in role or "管家" in role:
                is_ceo = True
            if not is_ceo:
                continue
            mems = await registry.current_memory(ident.id, kind="duty")
            current = " ".join(
                str(getattr(m, "content", "") or "") for m in (mems or [])
            )
            if _CEO_DUTY_MARKER in current:
                continue
            if mems:
                old = mems[0]
                await registry.supersede_memory(
                    old.id, new_duty, approved_by="soft_update_ceo_duty"
                )
            else:
                await registry.append_memory(
                    ident.id,
                    "duty",
                    new_duty,
                    source="system",
                    approved_by="soft_update_ceo_duty",
                )
            updated.append(name or str(ident.id)[:8])
            logger.info("soft-updated CEO duty name=%s", name[:24])
        except Exception as e:
            logger.debug("soft_update_ceo_duty skip: %s", e)
    return updated


async def seed_template_crew(
    registry: Any,
    *,
    user_id: Any = None,
    include_workers: bool = True,
) -> dict[str, Any]:
    """幂等预置：CEO 必有；include_workers 时补全 auto_seed 同事。

    兼容旧种子「小白」：若已有旧 CEO 名则视为 CEO 已存在，不再建第二个。
    Always soft-refreshes CEO duty to solo-default wording when outdated.
    """
    created: list[dict[str, Any]] = []
    skipped: list[str] = []
    items = await registry.list(status=None)
    existing_names = {i.name for i in items}

    # CEO 是否已在编（含旧名）；顺带给旧管家打上 is_ceo 标记便于前端徽标
    has_ceo = bool(existing_names & _LEGACY_CEO_NAMES)
    if not has_ceo:
        for i in items:
            meta = getattr(i, "meta", None) or {}
            if isinstance(meta, dict) and (
                meta.get("is_ceo") or meta.get("template_id") == "ceo"
            ):
                has_ceo = True
                break
            role = str(getattr(i, "role", "") or "").lower()
            if "ceo" in role or "管家" in role:
                has_ceo = True
                await _tag_legacy_ceo(registry, i)
                break
    else:
        for i in items:
            if i.name in _LEGACY_CEO_NAMES:
                await _tag_legacy_ceo(registry, i)
                break

    to_seed = [CEO_TEMPLATE] if not has_ceo else []
    if include_workers:
        for t in WORKER_TEMPLATES:
            if not t.get("auto_seed"):
                continue
            if t["name"] in existing_names:
                skipped.append(t["name"])
                continue
            to_seed.append(t)
    elif has_ceo:
        skipped.append(CEO_TEMPLATE["name"])

    if has_ceo and CEO_TEMPLATE["name"] not in [x["name"] for x in to_seed]:
        # 有旧 CEO 则跳过新建
        if CEO_TEMPLATE["name"] not in skipped and (
            existing_names & _LEGACY_CEO_NAMES
            or CEO_TEMPLATE["name"] in existing_names
        ):
            skipped.append(CEO_TEMPLATE["name"])

    for t in to_seed:
        if t.get("is_ceo") and has_ceo:
            continue
        if t["name"] in existing_names and not t.get("is_ceo"):
            skipped.append(t["name"])
            continue
        try:
            r = await _hire_one(
                registry, t, user_id=user_id, allow_rename=False
            )
            if r.get("status") == "created":
                created.append(
                    {
                        "name": r["name"],
                        "id": r["id"],
                        "role": r["role"],
                        "template_id": r.get("template_id"),
                    }
                )
                existing_names.add(r["name"])
                if t.get("is_ceo"):
                    has_ceo = True
            else:
                skipped.append(r.get("name") or t["name"])
        except ValueError as e:
            # 重名竞态
            skipped.append(t["name"])
            logger.debug("seed skip %s: %s", t["name"], e)
        except Exception as e:
            logger.warning("seed hire failed %s: %s", t["name"], e)

    duty_updated = await soft_update_ceo_duty(registry)

    total = len(await registry.list(status=None))
    return {
        "ok": True,
        "created": created,
        "skipped": skipped,
        "duty_updated": duty_updated,
        "total_after": total,
        "message": (
            f"hired {len(created)}, skipped {len(skipped)}"
            + (f", duty refreshed {len(duty_updated)}" if duty_updated else "")
            if created or skipped or duty_updated
            else "nothing to do"
        ),
    }


async def hire_from_template(
    registry: Any,
    template_id: str,
    *,
    user_id: Any = None,
    name: str | None = None,
) -> dict[str, Any]:
    """一键从模板雇佣（允许改名避冲突）。"""
    t = _template_by_id(template_id)
    if t is None:
        return {"ok": False, "error": f"unknown template_id: {template_id}"}
    r = await _hire_one(
        registry,
        t,
        user_id=user_id,
        name_override=name,
        allow_rename=True,
    )
    if r.get("status") != "created":
        return {"ok": False, "error": r.get("reason") or "hire failed", **r}
    return {"ok": True, "identity": r}


async def seed_default_crew_on_startup() -> dict[str, Any]:
    """Lifespan 调用：确保 CEO + 默认同事存在。"""
    from backend.database import AsyncSessionLocal
    from backend.kernel.kernel import get_kernel
    from backend.repositories.user_repo import AsyncUserRepository

    kernel = get_kernel()
    reg = getattr(kernel, "identity_registry", None)
    if reg is None:
        from backend.kernel.identity import IdentityRegistry

        kernel.identity_registry = IdentityRegistry(kernel, AsyncSessionLocal)
        reg = kernel.identity_registry

    user_id = None
    try:
        admin = await AsyncUserRepository().get_by_email("admin@tevarn.dev")
        if admin is not None:
            user_id = admin.id
    except Exception:
        pass

    return await seed_template_crew(reg, user_id=user_id, include_workers=True)


async def main() -> None:
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if root not in sys.path:
        sys.path.insert(0, root)

    from backend.database import init_db

    await init_db()
    result = await seed_default_crew_on_startup()
    for c in result.get("created") or []:
        print(f"hired: {c['name']} id={c['id']}")
    for name in result.get("skipped") or []:
        print(f"skip exists: {name}")
    print(result.get("message", "done"))


if __name__ == "__main__":
    asyncio.run(main())
