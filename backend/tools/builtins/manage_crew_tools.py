"""Crew/identity management tools: sub_agent, skill, profile."""
from __future__ import annotations

import logging
from typing import Any

from backend.tools.base import BaseTool, ToolRiskLevel, ToolSource
from backend.tools.builtins.manage_common import (
    _enroll_identity_for_subagent,
    _iso,
    _parse_uuid,
)
from backend.tools.builtins.self_config import ToolResult

logger = logging.getLogger(__name__)


# ── 子代理 ──

class ManageSubAgent(BaseTool):
    """子代理管理工具（对齐 sub_agents 路由 + AsyncSubAgentRepository）"""

    def __init__(self):
        super().__init__(
            name="manage_sub_agent",
            description=(
                "管理子代理技能包，并同步入编员工（Identity）。"
                "action: list/get/create/update/delete。"
                "create 会同时写入员工列表（编制真源）；招人优先用 crew_steward.hire。"
                "create 需要 name 和 model_ref；tools 如 ['file','terminal','git']"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "get", "create", "update", "delete"],
                        "description": "操作类型",
                    },
                    "agent_id": {"type": "string", "description": "get/update/delete 时: 子代理 UUID"},
                    "name": {"type": "string", "description": "create/update: 子代理名称"},
                    "description": {"type": "string", "description": "create/update: 子代理描述"},
                    "icon": {"type": "string", "description": "create/update: 图标 emoji，默认 🤖"},
                    "model_ref": {"type": "string", "description": "create/update: 模型引用，格式 provider_id/model_name"},
                    "system_prompt": {"type": "string", "description": "create/update: 角色系统提示词"},
                    "tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "create/update: 启用的工具集，如 ['file','terminal','git']",
                    },
                    "max_iterations": {"type": "integer", "description": "create/update: 最大迭代次数，默认 5"},
                    "temperature": {"type": "number", "description": "create/update: 采样温度，默认 0.3"},
                    "enabled": {"type": "boolean", "description": "create/update: 是否启用"},
                },
                "required": ["action"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )

    def _to_dict(self, obj: Any) -> dict[str, Any]:
        return {
            "id": str(obj.id),
            "name": obj.name,
            "description": obj.description,
            "icon": obj.icon,
            "model_ref": obj.model_ref,
            "system_prompt": obj.system_prompt,
            "enabled_toolsets": obj.enabled_toolsets or [],
            "max_iterations": obj.max_iterations,
            "temperature": obj.temperature,
            "enabled": bool(obj.enabled),
            "sort_order": obj.sort_order,
            "is_builtin": bool(obj.is_builtin),
            "created_at": _iso(getattr(obj, "created_at", None)),
            "updated_at": _iso(getattr(obj, "updated_at", None)),
        }

    def _collect_patch(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        patch: dict[str, Any] = {}
        for key in ("name", "description", "icon", "model_ref", "system_prompt"):
            if kwargs.get(key) is not None:
                patch[key] = str(kwargs[key])
        if kwargs.get("tools") is not None:
            if not isinstance(kwargs["tools"], list):
                raise ValueError("tools 必须是字符串数组")
            patch["enabled_toolsets"] = [str(t) for t in kwargs["tools"]]
        if kwargs.get("max_iterations") is not None:
            n = int(kwargs["max_iterations"])
            if not 1 <= n <= 50:
                raise ValueError("max_iterations 需在 1-50 之间")
            patch["max_iterations"] = n
        if kwargs.get("temperature") is not None:
            t = float(kwargs["temperature"])
            if not 0.0 <= t <= 2.0:
                raise ValueError("temperature 需在 0.0-2.0 之间")
            patch["temperature"] = t
        if kwargs.get("enabled") is not None:
            patch["enabled"] = bool(kwargs["enabled"])
        return patch

    async def execute(self, action: str, **kwargs: Any) -> ToolResult:
        from backend.repositories.sub_agent_repo import AsyncSubAgentRepository

        repo = AsyncSubAgentRepository()

        if action == "list":
            try:
                agents = await repo.list_all()
                data = [self._to_dict(a) for a in agents]
                return ToolResult(success=True, data={"sub_agents": data, "count": len(data)}, message=f"共 {len(data)} 个子代理")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 列出失败: {e}")

        elif action == "get":
            agent_id = (kwargs.get("agent_id") or "").strip()
            if not agent_id:
                return ToolResult(success=False, data={}, message="get 需要提供 agent_id")
            try:
                obj = await repo.get_by_id(_parse_uuid(agent_id, "agent_id"))
                if obj is None:
                    return ToolResult(success=False, data={}, message="子代理不存在")
                return ToolResult(success=True, data=self._to_dict(obj), message=f"子代理 `{obj.name}`")
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 获取失败: {e}")

        elif action == "create":
            name = (kwargs.get("name") or "").strip()
            model_ref = (kwargs.get("model_ref") or "").strip()
            if not name or not model_ref:
                return ToolResult(success=False, data={}, message="create 需要提供 name 和 model_ref（格式 provider_id/model_name）")
            try:
                patch = self._collect_patch(kwargs)
                patch.update({"name": name, "model_ref": model_ref, "user_id": None, "is_builtin": False})
                obj = await repo.create(patch)
                # 双写编制：员工列表只认 Identity，不能只建 SubAgent
                role = str(kwargs.get("description") or kwargs.get("role") or name).strip()
                ident, note = await _enroll_identity_for_subagent(obj, role=role)
                data = self._to_dict(obj)
                if ident is not None:
                    data["identity_id"] = str(ident.id)
                    data["identity_enrolled"] = True
                else:
                    data["identity_enrolled"] = False
                return ToolResult(
                    success=True,
                    data=data,
                    message=f"✅ 子代理 `{name}` 已创建；{note}（员工列表应可见）",
                )
            except ValueError as e:
                return ToolResult(success=False, data={}, message=f"❌ {e}")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 创建失败: {e}")

        elif action == "update":
            agent_id = (kwargs.get("agent_id") or "").strip()
            if not agent_id:
                return ToolResult(success=False, data={}, message="update 需要提供 agent_id")
            try:
                aid = _parse_uuid(agent_id, "agent_id")
                patch = self._collect_patch(kwargs)
                if not patch:
                    return ToolResult(success=False, data={}, message="update 至少需要提供一项更新")
                existing = await repo.get_by_id(aid)
                if existing is None:
                    return ToolResult(success=False, data={}, message="子代理不存在")
                if existing.is_builtin:
                    # 与路由一致：内置模板只允许改这些字段
                    allowed = {"enabled", "model_ref", "system_prompt", "temperature", "max_iterations", "enabled_toolsets"}
                    patch = {k: v for k, v in patch.items() if k in allowed}
                    if not patch:
                        return ToolResult(success=False, data={}, message="内置子代理模板不允许修改名称/描述等字段，仅可改 enabled/model_ref/system_prompt/temperature/max_iterations/tools")
                obj = await repo.update(aid, patch)
                return ToolResult(success=True, data=self._to_dict(obj), message=f"✅ 子代理 `{agent_id}` 已更新")
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 更新失败: {e}")

        elif action == "delete":
            agent_id = (kwargs.get("agent_id") or "").strip()
            if not agent_id:
                return ToolResult(success=False, data={}, message="delete 需要提供 agent_id")
            try:
                aid = _parse_uuid(agent_id, "agent_id")
                existing = await repo.get_by_id(aid)
                if existing is None:
                    return ToolResult(success=False, data={}, message="子代理不存在")
                if existing.is_builtin:
                    return ToolResult(success=False, data={}, message="内置子代理模板不允许删除")
                await repo.delete(aid)
                return ToolResult(success=True, data={"agent_id": agent_id}, message=f"✅ 子代理 `{agent_id}` 已删除")
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 删除失败: {e}")

        return ToolResult(success=False, data={}, message=f"未知 action: {action}")


# ── 技能 ──

class ManageSkill(BaseTool):
    """技能管理工具（对齐 skills 路由 + AsyncSkillRepository）"""

    def __init__(self):
        super().__init__(
            name="manage_skill",
            description=(
                "管理 Agent 技能（Skill）。action: list/get/create/update/delete/enable/disable。"
                "create 需要 name 和 schema（function calling JSON Schema）"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "get", "create", "update", "delete", "enable", "disable"],
                        "description": "操作类型",
                    },
                    "skill_id": {"type": "string", "description": "get/update/delete/enable/disable 时: 技能 UUID（与 name 二选一）"},
                    "name": {"type": "string", "description": "create: 技能名称；其他 action 可按名称定位"},
                    "description": {"type": "string", "description": "create/update: 技能描述"},
                    "schema": {"type": "object", "description": "create/update: function calling 的 JSON Schema"},
                    "handler": {"type": "string", "description": "create/update: 处理器类型 http | python"},
                    "handler_config": {"type": "object", "description": "create/update: 处理器配置"},
                    "enabled": {"type": "boolean", "description": "create/update: 是否启用"},
                },
                "required": ["action"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )

    def _to_dict(self, obj: Any) -> dict[str, Any]:
        return {
            "id": str(obj.id),
            "name": obj.name,
            "description": obj.description,
            "schema": obj.schema or {},
            "handler": obj.handler,
            "handler_config": obj.handler_config or {},
            "enabled": bool(obj.enabled),
            "is_builtin": bool(obj.is_builtin),
            "created_at": _iso(getattr(obj, "created_at", None)),
            "updated_at": _iso(getattr(obj, "updated_at", None)),
        }

    async def _resolve(self, repo: Any, kwargs: dict[str, Any]) -> Any | None:
        """按 skill_id 或 name 定位技能"""
        skill_id = (kwargs.get("skill_id") or "").strip()
        if skill_id:
            return await repo.get_by_id(_parse_uuid(skill_id, "skill_id"))
        name = (kwargs.get("name") or "").strip()
        if name:
            return await repo.get_skill_by_name(name)
        raise ValueError("需要提供 skill_id 或 name")

    async def execute(self, action: str, **kwargs: Any) -> ToolResult:
        from backend.repositories.skill_repo import AsyncSkillRepository

        repo = AsyncSkillRepository()

        if action == "list":
            try:
                from sqlalchemy import select

                from backend.database import get_db_context
                from backend.models.skill import Skill

                async with get_db_context() as db:
                    result = await db.execute(select(Skill).order_by(Skill.name))
                    skills = result.scalars().all()
                data = [self._to_dict(s) for s in skills]
                return ToolResult(success=True, data={"skills": data, "count": len(data)}, message=f"共 {len(data)} 个技能")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 列出失败: {e}")

        elif action == "get":
            try:
                obj = await self._resolve(repo, kwargs)
                if obj is None:
                    return ToolResult(success=False, data={}, message="技能不存在")
                return ToolResult(success=True, data=self._to_dict(obj), message=f"技能 `{obj.name}`")
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 获取失败: {e}")

        elif action == "create":
            name = (kwargs.get("name") or "").strip()
            schema = kwargs.get("schema")
            if not name:
                return ToolResult(success=False, data={}, message="create 需要提供 name")
            if schema is not None and not isinstance(schema, dict):
                return ToolResult(success=False, data={}, message="schema 必须是 JSON 对象")
            handler = (kwargs.get("handler") or "http").strip()
            if handler not in ("http", "python"):
                return ToolResult(success=False, data={}, message="handler 必须是 http 或 python")
            try:
                if await repo.get_skill_by_name(name):
                    return ToolResult(success=False, data={}, message=f"技能 `{name}` 已存在")
                obj = await repo.create({
                    "name": name,
                    "description": kwargs.get("description") or "",
                    "schema": schema or {},
                    "handler": handler,
                    "handler_config": kwargs.get("handler_config") or {},
                    "enabled": bool(kwargs.get("enabled", True)),
                    "is_builtin": False,
                })
                return ToolResult(success=True, data=self._to_dict(obj), message=f"✅ 技能 `{name}` 已创建")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 创建失败: {e}")

        elif action == "update":
            try:
                obj = await self._resolve(repo, kwargs)
                if obj is None:
                    return ToolResult(success=False, data={}, message="技能不存在")
                patch: dict[str, Any] = {}
                if kwargs.get("description") is not None:
                    patch["description"] = str(kwargs["description"])
                if kwargs.get("schema") is not None:
                    if not isinstance(kwargs["schema"], dict):
                        return ToolResult(success=False, data={}, message="schema 必须是 JSON 对象")
                    patch["schema"] = kwargs["schema"]
                if kwargs.get("handler") is not None:
                    h = str(kwargs["handler"]).strip()
                    if h not in ("http", "python"):
                        return ToolResult(success=False, data={}, message="handler 必须是 http 或 python")
                    patch["handler"] = h
                if kwargs.get("handler_config") is not None:
                    if not isinstance(kwargs["handler_config"], dict):
                        return ToolResult(success=False, data={}, message="handler_config 必须是 JSON 对象")
                    patch["handler_config"] = kwargs["handler_config"]
                if kwargs.get("enabled") is not None:
                    patch["enabled"] = bool(kwargs["enabled"])
                if not patch:
                    return ToolResult(success=False, data={}, message="update 至少需要提供一项更新")
                obj = await repo.update(obj.id, patch)
                return ToolResult(success=True, data=self._to_dict(obj), message=f"✅ 技能 `{obj.name}` 已更新")
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 更新失败: {e}")

        elif action == "delete":
            try:
                obj = await self._resolve(repo, kwargs)
                if obj is None:
                    return ToolResult(success=False, data={}, message="技能不存在")
                if obj.is_builtin:
                    return ToolResult(success=False, data={}, message="内置技能不允许删除，可用 disable 禁用")
                await repo.delete(obj.id)
                return ToolResult(success=True, data={"skill_id": str(obj.id)}, message=f"✅ 技能 `{obj.name}` 已删除")
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 删除失败: {e}")

        elif action in ("enable", "disable"):
            try:
                obj = await self._resolve(repo, kwargs)
                if obj is None:
                    return ToolResult(success=False, data={}, message="技能不存在")
                new_enabled = action == "enable"
                skill_name = obj.name
                obj = await repo.toggle_skill(obj.id, new_enabled)
                # 同步 ToolRegistry，避免 UI/工具切换后 schema 仍可见或不可见
                try:
                    from backend.tools.registry import ToolRegistry

                    rt = ToolRegistry.get(skill_name)
                    if rt is not None:
                        rt.enabled = new_enabled
                    elif new_enabled and obj is not None and not getattr(obj, "is_builtin", False):
                        from backend.skills.dynamic import DynamicSkill
                        from backend.tools.adapters.dynamic_adapter import DynamicSkillAdapter

                        ToolRegistry.register(DynamicSkillAdapter(DynamicSkill.from_db(obj)))
                except Exception:
                    pass
                return ToolResult(
                    success=True,
                    data=self._to_dict(obj) if obj else {"enabled": new_enabled},
                    message=f"✅ 技能已{'启用' if new_enabled else '禁用'}",
                )
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 切换失败: {e}")

        return ToolResult(success=False, data={}, message=f"未知 action: {action}")


# ── MCP Server ──

class ManageProfile(BaseTool):
    """Agent 角色画像管理工具（对齐 agent_profiles 路由 + AsyncAgentProfileRepository）"""

    def __init__(self):
        super().__init__(
            name="manage_profile",
            description=(
                "管理 Agent 角色画像（AgentProfile）。action: list/get/create/update/delete/set_default。"
                "create 需要 name；画像定义 Agent 的身份、系统提示词与技能组合"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "get", "create", "update", "delete", "set_default"],
                        "description": "操作类型",
                    },
                    "profile_id": {"type": "string", "description": "get/update/delete/set_default 时: 画像 UUID（与 name 二选一）"},
                    "name": {"type": "string", "description": "create/update: 画像名称；其他 action 可按名称定位"},
                    "identity": {"type": "string", "description": "create/update: 身份定义（一句话角色）"},
                    "sys_prompt": {"type": "string", "description": "create/update: 系统提示词"},
                    "agent_md": {"type": "string", "description": "create/update: AGENT.md 内容"},
                    "skills": {"type": "array", "items": {"type": "string"}, "description": "create/update: 关联技能名称列表"},
                    "config": {"type": "object", "description": "create/update: 扩展配置"},
                },
                "required": ["action"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )

    def _to_dict(self, obj: Any) -> dict[str, Any]:
        return {
            "id": str(obj.id),
            "name": obj.name,
            "identity": obj.identity,
            "sys_prompt": obj.sys_prompt,
            "agent_md": obj.agent_md,
            "skills": obj.skills or [],
            "is_default": bool(obj.is_default),
            "config": obj.config or {},
            "created_at": _iso(getattr(obj, "created_at", None)),
            "updated_at": _iso(getattr(obj, "updated_at", None)),
        }

    async def _resolve(self, repo: Any, kwargs: dict[str, Any]) -> Any | None:
        """按 profile_id 或 name 定位画像"""
        profile_id = (kwargs.get("profile_id") or "").strip()
        if profile_id:
            return await repo.get_by_id(_parse_uuid(profile_id, "profile_id"))
        name = (kwargs.get("name") or "").strip()
        if name:
            return await repo.get_by_name(name)
        raise ValueError("需要提供 profile_id 或 name")

    def _collect_patch(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        patch: dict[str, Any] = {}
        for key in ("name", "identity", "sys_prompt", "agent_md"):
            if kwargs.get(key) is not None:
                patch[key] = str(kwargs[key])
        if kwargs.get("skills") is not None:
            if not isinstance(kwargs["skills"], list):
                raise ValueError("skills 必须是字符串数组")
            patch["skills"] = [str(s) for s in kwargs["skills"]]
        if kwargs.get("config") is not None:
            if not isinstance(kwargs["config"], dict):
                raise ValueError("config 必须是 JSON 对象")
            patch["config"] = kwargs["config"]
        return patch

    async def execute(self, action: str, **kwargs: Any) -> ToolResult:
        from sqlalchemy.exc import IntegrityError

        from backend.repositories.agent_profile_repo import AsyncAgentProfileRepository

        repo = AsyncAgentProfileRepository()

        if action == "list":
            try:
                profiles = await repo.list_all()
                data = [self._to_dict(p) for p in profiles]
                return ToolResult(success=True, data={"profiles": data, "count": len(data)}, message=f"共 {len(data)} 个角色画像")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 列出失败: {e}")

        elif action == "get":
            try:
                obj = await self._resolve(repo, kwargs)
                if obj is None:
                    return ToolResult(success=False, data={}, message="画像不存在")
                return ToolResult(success=True, data=self._to_dict(obj), message=f"画像 `{obj.name}`")
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 获取失败: {e}")

        elif action == "create":
            name = (kwargs.get("name") or "").strip()
            if not name:
                return ToolResult(success=False, data={}, message="create 需要提供 name")
            try:
                if await repo.get_by_name(name):
                    return ToolResult(success=False, data={}, message=f"画像 `{name}` 已存在")
                patch = self._collect_patch(kwargs)
                patch.update({"name": name, "user_id": None})
                obj = await repo.create(patch)
                return ToolResult(success=True, data=self._to_dict(obj), message=f"✅ 画像 `{name}` 已创建")
            except ValueError as e:
                return ToolResult(success=False, data={}, message=f"❌ {e}")
            except IntegrityError:
                return ToolResult(success=False, data={}, message=f"❌ 画像 `{name}` 已存在")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 创建失败: {e}")

        elif action == "update":
            try:
                obj = await self._resolve(repo, kwargs)
                if obj is None:
                    return ToolResult(success=False, data={}, message="画像不存在")
                patch = self._collect_patch(kwargs)
                if not patch:
                    return ToolResult(success=False, data={}, message="update 至少需要提供一项更新")
                if "name" in patch and patch["name"] != obj.name:
                    existing = await repo.get_by_name(patch["name"])
                    if existing is not None and existing.id != obj.id:
                        return ToolResult(success=False, data={}, message=f"画像 `{patch['name']}` 已存在")
                try:
                    obj = await repo.update(obj.id, patch)
                except IntegrityError:
                    return ToolResult(success=False, data={}, message=f"❌ 画像 `{patch.get('name')}` 已存在")
                return ToolResult(success=True, data=self._to_dict(obj), message=f"✅ 画像 `{obj.name}` 已更新")
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 更新失败: {e}")

        elif action == "delete":
            try:
                obj = await self._resolve(repo, kwargs)
                if obj is None:
                    return ToolResult(success=False, data={}, message="画像不存在")
                await repo.delete(obj.id)
                return ToolResult(success=True, data={"profile_id": str(obj.id)}, message=f"✅ 画像 `{obj.name}` 已删除")
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 删除失败: {e}")

        elif action == "set_default":
            try:
                obj = await self._resolve(repo, kwargs)
                if obj is None:
                    return ToolResult(success=False, data={}, message="画像不存在")
                obj = await repo.set_default(obj.id)
                return ToolResult(
                    success=True,
                    data=self._to_dict(obj) if obj else {"is_default": True},
                    message="✅ 画像已设为默认",
                )
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 设置默认失败: {e}")

        return ToolResult(success=False, data={}, message=f"未知 action: {action}")


# ── 包管理 ──

