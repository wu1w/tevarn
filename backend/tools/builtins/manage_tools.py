"""Agent 资源管理工具集 — manage_sub_agent, manage_skill, manage_mcp, manage_channel, manage_webhook, query_audit_log, list_tasks, manage_profile, manage_package, query_evolution, manage_git, manage_evolution"""

from __future__ import annotations

import logging
import uuid as uuid_mod
from typing import Any

from backend.tools.base import BaseTool, ToolSource, ToolRiskLevel
from backend.tools.builtins.self_config import ToolResult

logger = logging.getLogger(__name__)


# ── 通用辅助 ──

def _parse_uuid(raw: str, field: str = "id") -> uuid_mod.UUID:
    """解析 UUID 字符串，失败抛 ValueError（由调用方转成失败结果）"""
    try:
        return uuid_mod.UUID(str(raw).strip())
    except (ValueError, AttributeError):
        raise ValueError(f"{field} 不是合法 UUID: {raw}")


def _iso(v: Any) -> str | None:
    return v.isoformat() if v else None


# ── 子代理 ──

class ManageSubAgent(BaseTool):
    """子代理管理工具（对齐 sub_agents 路由 + AsyncSubAgentRepository）"""

    def __init__(self):
        super().__init__(
            name="manage_sub_agent",
            description=(
                "管理子代理（SubAgent）。action: list/get/create/update/delete。"
                "create 需要 name 和 model_ref（格式 provider_id/model_name）；"
                "tools 为启用的工具集列表（如 ['file','terminal','git']）"
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
                return ToolResult(success=True, data=self._to_dict(obj), message=f"✅ 子代理 `{name}` 已创建")
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
                from backend.database import get_db_context
                from sqlalchemy import select
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
                obj = await repo.toggle_skill(obj.id, new_enabled)
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

class ManageMcp(BaseTool):
    """MCP Server 配置管理工具（对齐 mcp 路由 + AsyncMCPServerRepository）"""

    def __init__(self):
        super().__init__(
            name="manage_mcp",
            description=(
                "管理 MCP Server 配置。action: list/get/add/update/delete。"
                "add 需要 name 和 transport（stdio|sse）；stdio 需 command，sse 需 url"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "get", "add", "update", "delete"],
                        "description": "操作类型",
                    },
                    "server_id": {"type": "string", "description": "get/update/delete 时: MCP Server UUID（与 name 二选一）"},
                    "name": {"type": "string", "description": "add: 服务名称；get/update/delete 可按名称定位"},
                    "transport": {"type": "string", "description": "add/update: 传输方式 stdio | sse"},
                    "command": {"type": "string", "description": "add/update: stdio 启动命令"},
                    "args": {"type": "array", "items": {"type": "string"}, "description": "add/update: 启动参数"},
                    "url": {"type": "string", "description": "add/update: sse 服务地址"},
                    "env": {"type": "object", "description": "add/update: 环境变量"},
                    "enabled": {"type": "boolean", "description": "add/update: 是否启用"},
                    "timeout": {"type": "number", "description": "add/update: 超时秒数，默认 30"},
                    "risk_level": {"type": "string", "description": "add/update: 风险等级，默认 medium"},
                    "allowed_paths": {"type": "array", "items": {"type": "string"}, "description": "add/update: 允许访问的路径"},
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
            "transport": obj.transport,
            "command": obj.command,
            "args": obj.args or [],
            "url": obj.url,
            "env_keys": sorted((obj.env or {}).keys()),
            "enabled": bool(obj.enabled),
            "timeout": obj.timeout,
            "risk_level": obj.risk_level,
            "allowed_paths": obj.allowed_paths,
        }

    async def _resolve_id(self, repo: Any, kwargs: dict[str, Any]) -> uuid_mod.UUID:
        server_id = (kwargs.get("server_id") or "").strip()
        if server_id:
            return _parse_uuid(server_id, "server_id")
        name = (kwargs.get("name") or "").strip()
        if name:
            obj = await repo.get_by_name(name)
            if obj is None:
                raise ValueError(f"MCP Server 不存在: {name}")
            return obj.id
        raise ValueError("需要提供 server_id 或 name")

    async def execute(self, action: str, **kwargs: Any) -> ToolResult:
        from backend.repositories.mcp_server_repo import AsyncMCPServerRepository
        from backend.schemas.mcp import MCPServerCreate, MCPServerUpdate

        repo = AsyncMCPServerRepository()

        if action == "list":
            try:
                servers = await repo.list_all()
                data = [self._to_dict(s) for s in servers]
                return ToolResult(success=True, data={"servers": data, "count": len(data)}, message=f"共 {len(data)} 个 MCP Server")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 列出失败: {e}")

        elif action == "get":
            try:
                sid = await self._resolve_id(repo, kwargs)
                obj = await repo.get_by_id(sid)
                if obj is None:
                    return ToolResult(success=False, data={}, message="MCP Server 不存在")
                return ToolResult(success=True, data=self._to_dict(obj), message=f"MCP Server `{obj.name}`")
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 获取失败: {e}")

        elif action == "add":
            name = (kwargs.get("name") or "").strip()
            transport = (kwargs.get("transport") or "").strip()
            if not name or not transport:
                return ToolResult(success=False, data={}, message="add 需要提供 name 和 transport（stdio|sse）")
            if transport not in ("stdio", "sse"):
                return ToolResult(success=False, data={}, message="transport 必须是 stdio 或 sse")
            if transport == "stdio" and not (kwargs.get("command") or "").strip():
                return ToolResult(success=False, data={}, message="stdio 传输需要提供 command")
            if transport == "sse" and not (kwargs.get("url") or "").strip():
                return ToolResult(success=False, data={}, message="sse 传输需要提供 url")
            try:
                if await repo.get_by_name(name):
                    return ToolResult(success=False, data={}, message=f"MCP Server `{name}` 已存在")
                data = MCPServerCreate(
                    name=name,
                    transport=transport,
                    command=kwargs.get("command") or None,
                    args=[str(a) for a in (kwargs.get("args") or [])],
                    url=kwargs.get("url") or None,
                    env={str(k): str(v) for k, v in (kwargs.get("env") or {}).items()},
                    enabled=bool(kwargs.get("enabled", True)),
                    timeout=float(kwargs.get("timeout", 30.0)),
                    risk_level=str(kwargs.get("risk_level") or "medium"),
                    allowed_paths=kwargs.get("allowed_paths"),
                )
                obj = await repo.create(data)
                return ToolResult(success=True, data=self._to_dict(obj), message=f"✅ MCP Server `{name}` 已添加")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 添加失败: {e}")

        elif action == "update":
            try:
                sid = await self._resolve_id(repo, kwargs)
                patch: dict[str, Any] = {}
                if kwargs.get("name") is not None:
                    patch["name"] = str(kwargs["name"]).strip()
                if kwargs.get("transport") is not None:
                    t = str(kwargs["transport"]).strip()
                    if t not in ("stdio", "sse"):
                        return ToolResult(success=False, data={}, message="transport 必须是 stdio 或 sse")
                    patch["transport"] = t
                if kwargs.get("command") is not None:
                    patch["command"] = str(kwargs["command"])
                if kwargs.get("args") is not None:
                    patch["args"] = [str(a) for a in kwargs["args"]]
                if kwargs.get("url") is not None:
                    patch["url"] = str(kwargs["url"])
                if kwargs.get("env") is not None:
                    patch["env"] = {str(k): str(v) for k, v in kwargs["env"].items()}
                if kwargs.get("enabled") is not None:
                    patch["enabled"] = bool(kwargs["enabled"])
                if kwargs.get("timeout") is not None:
                    patch["timeout"] = float(kwargs["timeout"])
                if kwargs.get("risk_level") is not None:
                    patch["risk_level"] = str(kwargs["risk_level"])
                if kwargs.get("allowed_paths") is not None:
                    patch["allowed_paths"] = kwargs["allowed_paths"]
                if not patch:
                    return ToolResult(success=False, data={}, message="update 至少需要提供一项更新")
                obj = await repo.update(sid, MCPServerUpdate(**patch))
                if obj is None:
                    return ToolResult(success=False, data={}, message="MCP Server 不存在")
                return ToolResult(success=True, data=self._to_dict(obj), message=f"✅ MCP Server `{obj.name}` 已更新")
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 更新失败: {e}")

        elif action == "delete":
            try:
                sid = await self._resolve_id(repo, kwargs)
                ok = await repo.delete(sid)
                if not ok:
                    return ToolResult(success=False, data={}, message="MCP Server 不存在")
                return ToolResult(success=True, data={"server_id": str(sid)}, message=f"✅ MCP Server `{sid}` 已删除")
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 删除失败: {e}")

        return ToolResult(success=False, data={}, message=f"未知 action: {action}")


# ── 消息通道 ──

class ManageChannel(BaseTool):
    """消息通道管理工具（对齐 channels 路由，直连 Channel 模型）"""

    def __init__(self):
        super().__init__(
            name="manage_channel",
            description=(
                "管理 IM 消息通道（Telegram/Discord/企业微信/QQ/Slack/飞书/钉钉等）。"
                "action: list/get/create/update/delete。create 需要 platform 和 name"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "get", "create", "update", "delete"],
                        "description": "操作类型",
                    },
                    "channel_id": {"type": "string", "description": "get/update/delete 时: 通道 UUID"},
                    "platform": {"type": "string", "description": "create: 平台标识，如 telegram/discord/wecom/qqbot/slack/feishu/dingtalk/signal"},
                    "name": {"type": "string", "description": "create/update: 通道显示名称"},
                    "description": {"type": "string", "description": "create/update: 通道描述"},
                    "enabled": {"type": "boolean", "description": "create/update: 是否启用"},
                    "token": {"type": "string", "description": "create/update: Bot Token（敏感，不会回显）"},
                    "api_key": {"type": "string", "description": "create/update: API Key / App Secret（敏感，不会回显）"},
                    "home_channel_id": {"type": "string", "description": "create/update: 主频道 ID"},
                    "extra": {"type": "object", "description": "create/update: 平台特有配置"},
                    "webhook_url": {"type": "string", "description": "create/update: 回调地址"},
                },
                "required": ["action"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )

    def _to_dict(self, ch: Any) -> dict[str, Any]:
        return {
            "id": str(ch.id),
            "platform": ch.platform,
            "name": ch.name,
            "description": ch.description,
            "enabled": bool(ch.enabled),
            "connected": bool(ch.connected),
            "home_channel_id": ch.home_channel_id,
            "extra": ch.extra or {},
            "webhook_url": ch.webhook_url,
            "last_tested_at": ch.last_tested_at,
            "last_test_result": ch.last_test_result,
            "has_token": bool(ch.token),
            "has_api_key": bool(ch.api_key),
            "created_at": _iso(getattr(ch, "created_at", None)),
            "updated_at": _iso(getattr(ch, "updated_at", None)),
        }

    async def execute(self, action: str, **kwargs: Any) -> ToolResult:
        from sqlalchemy import select

        from backend.database import get_db_context
        from backend.models.channel import Channel

        if action == "list":
            try:
                async with get_db_context() as db:
                    result = await db.execute(select(Channel).order_by(Channel.created_at.desc()))
                    channels = result.scalars().all()
                data = [self._to_dict(ch) for ch in channels]
                return ToolResult(success=True, data={"channels": data, "count": len(data)}, message=f"共 {len(data)} 个消息通道")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 列出失败: {e}")

        elif action == "get":
            channel_id = (kwargs.get("channel_id") or "").strip()
            if not channel_id:
                return ToolResult(success=False, data={}, message="get 需要提供 channel_id")
            try:
                async with get_db_context() as db:
                    ch = await db.get(Channel, _parse_uuid(channel_id, "channel_id"))
                    if ch is None:
                        return ToolResult(success=False, data={}, message="通道不存在")
                    return ToolResult(success=True, data=self._to_dict(ch), message=f"通道 `{ch.name}`")
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 获取失败: {e}")

        elif action == "create":
            platform = (kwargs.get("platform") or "").strip()
            name = (kwargs.get("name") or "").strip()
            if not platform or not name:
                return ToolResult(success=False, data={}, message="create 需要提供 platform 和 name")
            try:
                async with get_db_context() as db:
                    ch = Channel(
                        platform=platform,
                        name=name,
                        description=kwargs.get("description"),
                        enabled=bool(kwargs.get("enabled", False)),
                        token=kwargs.get("token") or None,
                        api_key=kwargs.get("api_key") or None,
                        home_channel_id=kwargs.get("home_channel_id"),
                        extra=kwargs.get("extra") or {},
                        webhook_url=kwargs.get("webhook_url"),
                    )
                    db.add(ch)
                    await db.commit()
                    await db.refresh(ch)
                    logger.info("manage_channel created: %s (%s)", ch.name, ch.platform)
                    return ToolResult(success=True, data=self._to_dict(ch), message=f"✅ 通道 `{name}` 已创建")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 创建失败: {e}")

        elif action == "update":
            channel_id = (kwargs.get("channel_id") or "").strip()
            if not channel_id:
                return ToolResult(success=False, data={}, message="update 需要提供 channel_id")
            try:
                async with get_db_context() as db:
                    ch = await db.get(Channel, _parse_uuid(channel_id, "channel_id"))
                    if ch is None:
                        return ToolResult(success=False, data={}, message="通道不存在")
                    patch: dict[str, Any] = {}
                    for key in ("name", "description", "home_channel_id", "webhook_url"):
                        if kwargs.get(key) is not None:
                            patch[key] = str(kwargs[key])
                    if kwargs.get("enabled") is not None:
                        patch["enabled"] = bool(kwargs["enabled"])
                    if kwargs.get("extra") is not None:
                        if not isinstance(kwargs["extra"], dict):
                            return ToolResult(success=False, data={}, message="extra 必须是 JSON 对象")
                        patch["extra"] = kwargs["extra"]
                    # 与路由一致：空字符串表示清除密钥，缺省表示不改动
                    for key in ("token", "api_key"):
                        if kwargs.get(key) is not None:
                            patch[key] = str(kwargs[key]) or None
                    if not patch:
                        return ToolResult(success=False, data={}, message="update 至少需要提供一项更新")
                    for k, v in patch.items():
                        setattr(ch, k, v)
                    await db.commit()
                    await db.refresh(ch)
                    logger.info("manage_channel updated: %s (%s)", ch.name, ch.platform)
                    return ToolResult(success=True, data=self._to_dict(ch), message=f"✅ 通道 `{ch.name}` 已更新")
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 更新失败: {e}")

        elif action == "delete":
            channel_id = (kwargs.get("channel_id") or "").strip()
            if not channel_id:
                return ToolResult(success=False, data={}, message="delete 需要提供 channel_id")
            try:
                async with get_db_context() as db:
                    ch = await db.get(Channel, _parse_uuid(channel_id, "channel_id"))
                    if ch is None:
                        return ToolResult(success=False, data={}, message="通道不存在")
                    await db.delete(ch)
                    await db.commit()
                    logger.info("manage_channel deleted: %s (%s)", ch.name, ch.platform)
                    return ToolResult(success=True, data={"channel_id": channel_id}, message=f"✅ 通道 `{channel_id}` 已删除")
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 删除失败: {e}")

        return ToolResult(success=False, data={}, message=f"未知 action: {action}")


# ── Webhook ──

class ManageWebhook(BaseTool):
    """Webhook 管理工具（对齐 webhook 路由 + AsyncWebhookRepository）"""

    def __init__(self):
        super().__init__(
            name="manage_webhook",
            description=(
                "管理 Webhook（出站回调/订阅事件触发工作流）。action: list/get/create/update/delete。"
                "create 需要 name 和 url"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "get", "create", "update", "delete"],
                        "description": "操作类型",
                    },
                    "webhook_id": {"type": "string", "description": "get/update/delete 时: Webhook UUID"},
                    "name": {"type": "string", "description": "create/update: Webhook 名称"},
                    "url": {"type": "string", "description": "create/update: 目标 URL"},
                    "secret": {"type": "string", "description": "create/update: 签名密钥（敏感，不会回显）"},
                    "events": {"type": "array", "items": {"type": "string"}, "description": "create/update: 订阅事件列表"},
                    "workflow_id": {"type": "string", "description": "create/update: 触发的工作流 UUID"},
                    "enabled": {"type": "boolean", "description": "create/update: 是否启用"},
                    "headers": {"type": "object", "description": "create/update: 自定义请求头"},
                    "retry_on_failure": {"type": "boolean", "description": "create/update: 失败是否重试"},
                    "max_retries": {"type": "integer", "description": "create/update: 最大重试次数，默认 3"},
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
            "url": obj.url,
            "events": obj.events or [],
            "workflow_id": str(obj.workflow_id) if obj.workflow_id else None,
            "enabled": bool(obj.enabled),
            "headers": obj.headers or {},
            "retry_on_failure": bool(obj.retry_on_failure),
            "max_retries": obj.max_retries,
            "has_secret": bool(obj.secret),
            "last_status": obj.last_status,
            "trigger_count": obj.trigger_count,
            "last_triggered_at": _iso(getattr(obj, "last_triggered_at", None)),
            "created_at": _iso(getattr(obj, "created_at", None)),
            "updated_at": _iso(getattr(obj, "updated_at", None)),
        }

    def _collect_patch(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        patch: dict[str, Any] = {}
        for key in ("name", "url", "secret"):
            if kwargs.get(key) is not None:
                patch[key] = str(kwargs[key])
        if kwargs.get("events") is not None:
            if not isinstance(kwargs["events"], list):
                raise ValueError("events 必须是字符串数组")
            patch["events"] = [str(e) for e in kwargs["events"]]
        if kwargs.get("workflow_id") is not None:
            raw = str(kwargs["workflow_id"]).strip()
            patch["workflow_id"] = _parse_uuid(raw, "workflow_id") if raw else None
        if kwargs.get("enabled") is not None:
            patch["enabled"] = bool(kwargs["enabled"])
        if kwargs.get("headers") is not None:
            if not isinstance(kwargs["headers"], dict):
                raise ValueError("headers 必须是 JSON 对象")
            patch["headers"] = kwargs["headers"]
        if kwargs.get("retry_on_failure") is not None:
            patch["retry_on_failure"] = bool(kwargs["retry_on_failure"])
        if kwargs.get("max_retries") is not None:
            patch["max_retries"] = int(kwargs["max_retries"])
        return patch

    async def execute(self, action: str, **kwargs: Any) -> ToolResult:
        from backend.repositories.webhook_repo import AsyncWebhookRepository

        repo = AsyncWebhookRepository()

        if action == "list":
            try:
                from sqlalchemy import select

                from backend.database import get_db_context
                from backend.models.webhook import Webhook

                async with get_db_context() as db:
                    result = await db.execute(select(Webhook).order_by(Webhook.name))
                    hooks = result.scalars().all()
                data = [self._to_dict(h) for h in hooks]
                return ToolResult(success=True, data={"webhooks": data, "count": len(data)}, message=f"共 {len(data)} 个 Webhook")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 列出失败: {e}")

        elif action == "get":
            webhook_id = (kwargs.get("webhook_id") or "").strip()
            if not webhook_id:
                return ToolResult(success=False, data={}, message="get 需要提供 webhook_id")
            try:
                obj = await repo.get_by_id(_parse_uuid(webhook_id, "webhook_id"))
                if obj is None:
                    return ToolResult(success=False, data={}, message="Webhook 不存在")
                return ToolResult(success=True, data=self._to_dict(obj), message=f"Webhook `{obj.name}`")
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 获取失败: {e}")

        elif action == "create":
            name = (kwargs.get("name") or "").strip()
            url = (kwargs.get("url") or "").strip()
            if not name or not url:
                return ToolResult(success=False, data={}, message="create 需要提供 name 和 url")
            try:
                patch = self._collect_patch(kwargs)
                patch.update({"name": name, "url": url, "user_id": None})
                obj = await repo.create(patch)
                return ToolResult(success=True, data=self._to_dict(obj), message=f"✅ Webhook `{name}` 已创建")
            except ValueError as e:
                return ToolResult(success=False, data={}, message=f"❌ {e}")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 创建失败: {e}")

        elif action == "update":
            webhook_id = (kwargs.get("webhook_id") or "").strip()
            if not webhook_id:
                return ToolResult(success=False, data={}, message="update 需要提供 webhook_id")
            try:
                wid = _parse_uuid(webhook_id, "webhook_id")
                patch = self._collect_patch(kwargs)
                if not patch:
                    return ToolResult(success=False, data={}, message="update 至少需要提供一项更新")
                obj = await repo.update(wid, patch)
                if obj is None:
                    return ToolResult(success=False, data={}, message="Webhook 不存在")
                return ToolResult(success=True, data=self._to_dict(obj), message=f"✅ Webhook `{obj.name}` 已更新")
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 更新失败: {e}")

        elif action == "delete":
            webhook_id = (kwargs.get("webhook_id") or "").strip()
            if not webhook_id:
                return ToolResult(success=False, data={}, message="delete 需要提供 webhook_id")
            try:
                wid = _parse_uuid(webhook_id, "webhook_id")
                ok = await repo.delete(wid)
                if not ok:
                    return ToolResult(success=False, data={}, message="Webhook 不存在")
                return ToolResult(success=True, data={"webhook_id": webhook_id}, message=f"✅ Webhook `{webhook_id}` 已删除")
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 删除失败: {e}")

        return ToolResult(success=False, data={}, message=f"未知 action: {action}")


# ── 审计日志查询（只读） ──

class QueryAuditLog(BaseTool):
    """审计日志查询工具（只读）"""

    def __init__(self):
        super().__init__(
            name="query_audit_log",
            description="查询安全审计日志，支持按 action/资源/用户过滤，按时间倒序返回",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "按操作类型过滤，如 login/update_config"},
                    "resource_type": {"type": "string", "description": "按资源类型过滤，如 sub_agent/skill"},
                    "resource_id": {"type": "string", "description": "按资源 ID 过滤"},
                    "user_id": {"type": "string", "description": "按用户 UUID 过滤"},
                    "limit": {"type": "integer", "description": "返回条数，默认 50，最大 500"},
                    "offset": {"type": "integer", "description": "分页偏移，默认 0"},
                },
                "required": [],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.SAFE,
        )

    def _to_dict(self, obj: Any) -> dict[str, Any]:
        return {
            "id": str(obj.id),
            "action": obj.action,
            "resource_type": obj.resource_type,
            "resource_id": obj.resource_id,
            "user_id": str(obj.user_id) if obj.user_id else None,
            "success": bool(obj.success),
            "details": obj.details or {},
            "ip_address": obj.ip_address,
            "created_at": _iso(getattr(obj, "created_at", None)),
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        from sqlalchemy import desc, select

        from backend.database import get_db_context
        from backend.models.audit_log import AuditLog

        try:
            limit = int(kwargs.get("limit", 50) or 50)
            offset = int(kwargs.get("offset", 0) or 0)
        except (TypeError, ValueError):
            return ToolResult(success=False, data={}, message="limit/offset 必须是整数")
        limit = max(1, min(limit, 500))
        offset = max(0, offset)

        try:
            stmt = select(AuditLog)
            action = (kwargs.get("action") or "").strip()
            resource_type = (kwargs.get("resource_type") or "").strip()
            resource_id = (kwargs.get("resource_id") or "").strip()
            user_id = (kwargs.get("user_id") or "").strip()
            if action:
                stmt = stmt.where(AuditLog.action == action)
            if resource_type:
                stmt = stmt.where(AuditLog.resource_type == resource_type)
            if resource_id:
                stmt = stmt.where(AuditLog.resource_id == resource_id)
            if user_id:
                try:
                    stmt = stmt.where(AuditLog.user_id == _parse_uuid(user_id, "user_id"))
                except ValueError as e:
                    return ToolResult(success=False, data={}, message=str(e))
            stmt = stmt.order_by(desc(AuditLog.created_at)).offset(offset).limit(limit)

            async with get_db_context() as db:
                result = await db.execute(stmt)
                logs = result.scalars().all()
            data = [self._to_dict(log) for log in logs]
            return ToolResult(success=True, data={"logs": data, "count": len(data)}, message=f"查询到 {len(data)} 条审计日志")
        except Exception as e:
            return ToolResult(success=False, data={}, message=f"❌ 查询失败: {e}")


# ── 任务查询（只读） ──

class ListTasks(BaseTool):
    """任务查询工具（只读）— 列出会话任务或最近任务"""

    def __init__(self):
        super().__init__(
            name="list_tasks",
            description="查询异步任务执行记录。可按 session_id 列出会话任务，或不带参数列出最近任务；active_only=true 只看进行中任务",
            parameters={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "可选，会话 UUID；不提供则列出全局最近任务"},
                    "active_only": {"type": "boolean", "description": "仅看 pending/running 中的任务，默认 false"},
                    "limit": {"type": "integer", "description": "返回条数，默认 20，最大 100"},
                    "offset": {"type": "integer", "description": "分页偏移，默认 0"},
                },
                "required": [],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.SAFE,
        )

    def _to_dict(self, t: Any) -> dict[str, Any]:
        if hasattr(t, "model_dump"):
            return t.model_dump(mode="json")
        return {
            "id": str(t.id),
            "session_id": str(t.session_id),
            "name": t.name,
            "status": str(t.status),
            "progress": t.progress,
        }

    async def execute(self, **kwargs: Any) -> ToolResult:
        from backend.repositories.task_repo import AsyncTaskRepository

        repo = AsyncTaskRepository()

        try:
            limit = int(kwargs.get("limit", 20) or 20)
            offset = int(kwargs.get("offset", 0) or 0)
        except (TypeError, ValueError):
            return ToolResult(success=False, data={}, message="limit/offset 必须是整数")
        limit = max(1, min(limit, 100))
        offset = max(0, offset)

        session_id = (kwargs.get("session_id") or "").strip()
        active_only = bool(kwargs.get("active_only", False))

        try:
            if session_id:
                sid = _parse_uuid(session_id, "session_id")
                if active_only:
                    tasks = await repo.get_active_tasks_by_session(sid)
                else:
                    tasks = await repo.get_tasks_by_session(sid, limit=limit, offset=offset)
            else:
                from sqlalchemy import desc, select

                from backend.database import get_db_context
                from backend.models.task import Task, TaskStatus

                stmt = select(Task)
                if active_only:
                    stmt = stmt.where(Task.status.in_([TaskStatus.PENDING, TaskStatus.RUNNING]))
                stmt = stmt.order_by(desc(Task.created_at)).offset(offset).limit(limit)
                async with get_db_context() as db:
                    result = await db.execute(stmt)
                    tasks = result.scalars().all()

            data = [self._to_dict(t) for t in tasks]
            scope = f"会话 {session_id}" if session_id else "全部会话"
            return ToolResult(success=True, data={"tasks": data, "count": len(data)}, message=f"{scope} 共 {len(data)} 个任务")
        except ValueError as e:
            return ToolResult(success=False, data={}, message=str(e))
        except Exception as e:
            return ToolResult(success=False, data={}, message=f"❌ 查询失败: {e}")


# ── Agent 角色画像 ──

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
                    message=f"✅ 画像已设为默认",
                )
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 设置默认失败: {e}")

        return ToolResult(success=False, data={}, message=f"未知 action: {action}")


# ── 包管理 ──

class ManagePackage(BaseTool):
    """包管理工具（对齐 packages 路由：包来自工作区目录发现 + skill/sub_agent/workflow 虚拟投影）"""

    def __init__(self):
        super().__init__(
            name="manage_package",
            description=(
                "管理包（Package：工作区包 + skill/sub_agent/workflow 的虚拟包投影）。"
                "action: list(列出)/get(详情)/attach(挂载到会话)/detach(从会话卸载)/set_attached(整体设置会话挂载)。"
                "包本身是只读发现的，写操作仅作用于会话挂载状态"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "get", "attach", "detach", "set_attached"],
                        "description": "操作类型",
                    },
                    "name": {"type": "string", "description": "get/attach/detach 时: 包名，如 skill:xxx / sub_agent:uuid / 工作区包名"},
                    "session_id": {"type": "string", "description": "attach/detach/set_attached 必填；list 可选（用于标记已挂载）"},
                    "source": {"type": "string", "description": "list 时: 按来源过滤 workspace|skill|sub_agent|workflow"},
                    "packages": {"type": "array", "items": {"type": "string"}, "description": "set_attached 时: 挂载包名列表（整体替换）"},
                },
                "required": ["action"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )

    async def execute(self, action: str, **kwargs: Any) -> ToolResult:
        from backend.packages.loader import (
            get_package_by_name,
            list_all_packages,
            package_to_detail,
            package_to_list_item,
            resolve_attached_snippets,
        )
        from backend.packages.session_packages import (
            attach_package,
            detach_package,
            get_session_attached_packages,
            set_session_attached_packages,
        )

        session_id = (kwargs.get("session_id") or "").strip()
        name = (kwargs.get("name") or "").strip()

        if action == "list":
            try:
                pkgs = await list_all_packages()
                attached: list[str] = []
                if session_id:
                    attached = await get_session_attached_packages(session_id)
                att_set = set(attached)
                source = (kwargs.get("source") or "").strip()
                items = []
                for p in pkgs:
                    if source and p.source != source:
                        continue
                    items.append(package_to_list_item(p, attached=p.name in att_set).model_dump())
                return ToolResult(
                    success=True,
                    data={"packages": items, "attached": attached, "count": len(items)},
                    message=f"共 {len(items)} 个包" + (f"，会话已挂载 {len(attached)} 个" if session_id else ""),
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 列出失败: {e}")

        elif action == "get":
            if not name:
                return ToolResult(success=False, data={}, message="get 需要提供 name")
            try:
                pkgs = await list_all_packages()
                p = get_package_by_name(pkgs, name)
                if not p:
                    return ToolResult(success=False, data={}, message=f"包 `{name}` 不存在")
                attached = False
                if session_id:
                    attached = name in await get_session_attached_packages(session_id)
                detail = package_to_detail(p, attached=attached).model_dump()
                return ToolResult(success=True, data=detail, message=f"包 `{name}`（{p.source}{'·虚拟' if p.virtual else ''}）")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 获取失败: {e}")

        elif action in ("attach", "detach"):
            if not session_id or not name:
                return ToolResult(success=False, data={}, message=f"{action} 需要提供 session_id 和 name")
            try:
                if action == "attach":
                    pkgs = await list_all_packages()
                    if not get_package_by_name(pkgs, name):
                        return ToolResult(success=False, data={}, message=f"包 `{name}` 不存在")
                    attached = await attach_package(session_id, name)
                    snippets = await resolve_attached_snippets(attached)
                    return ToolResult(
                        success=True,
                        data={"attached": attached, "snippets": snippets},
                        message=f"✅ 已挂载包 `{name}`",
                    )
                attached = await detach_package(session_id, name)
                return ToolResult(
                    success=True,
                    data={"attached": attached},
                    message=f"✅ 已卸载包 `{name}`",
                )
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 操作失败: {e}")

        elif action == "set_attached":
            packages = kwargs.get("packages")
            if not session_id:
                return ToolResult(success=False, data={}, message="set_attached 需要提供 session_id")
            if packages is None or not isinstance(packages, list):
                return ToolResult(success=False, data={}, message="set_attached 需要提供 packages 数组")
            try:
                pkgs = await list_all_packages()
                known = {p.name for p in pkgs}
                unknown = [str(n) for n in packages if str(n) not in known]
                if unknown:
                    return ToolResult(success=False, data={}, message=f"未知包: {unknown}")
                attached = await set_session_attached_packages(session_id, [str(n) for n in packages])
                snippets = await resolve_attached_snippets(attached)
                return ToolResult(
                    success=True,
                    data={"attached": attached, "snippets": snippets},
                    message=f"✅ 会话挂载已更新（{len(attached)} 个包）",
                )
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 设置失败: {e}")

        return ToolResult(success=False, data={}, message=f"未知 action: {action}")


# ── 自进化资产查询（只读） ──

class QueryEvolution(BaseTool):
    """自进化资产查询工具（只读，对齐 evolution 路由的读端点）"""

    def __init__(self):
        super().__init__(
            name="query_evolution",
            description=(
                "查询自进化（Evolution）系统状态与资产。action: status(运行状态)/stats(统计)/"
                "list(资产列表，可按 kind/status/source 过滤)/get(单个资产)/tasks(演化任务)/clusters(聚类)/version(引擎版本)。只读"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "stats", "list", "get", "tasks", "clusters", "version"],
                        "description": "操作类型",
                    },
                    "asset_id": {"type": "string", "description": "get 时: 资产 ID"},
                    "kind": {"type": "string", "description": "list 时: 按资产类型过滤，如 skill/tool/playbook"},
                    "status": {"type": "string", "description": "list 时: 按状态过滤，如 active/disabled/draft"},
                    "source": {"type": "string", "description": "list 时: 按来源过滤，如 seed/auto"},
                    "unused_only": {"type": "boolean", "description": "list 时: 仅看未被使用的资产"},
                    "limit": {"type": "integer", "description": "list 时: 返回条数，默认 200，最大 500"},
                },
                "required": ["action"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.SAFE,
        )

    async def execute(self, action: str, **kwargs: Any) -> ToolResult:
        try:
            from backend.evolution import store
            from backend.evolution.manager import get_evolution_manager
        except Exception as e:
            return ToolResult(success=False, data={}, message=f"❌ 进化模块不可用: {e}")

        if action == "status":
            try:
                mgr = get_evolution_manager()
                data = mgr.status()
                return ToolResult(success=True, data=data, message="进化系统状态已获取")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 获取状态失败: {e}")

        elif action == "stats":
            try:
                get_evolution_manager().ensure_seeded()
                data = store.stats()
                return ToolResult(success=True, data=data, message="进化资产统计已获取")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 获取统计失败: {e}")

        elif action == "list":
            try:
                get_evolution_manager().ensure_seeded()
                try:
                    limit = int(kwargs.get("limit", 200) or 200)
                except (TypeError, ValueError):
                    return ToolResult(success=False, data={}, message="limit 必须是整数")
                limit = max(1, min(limit, 500))
                assets = store.list_assets(
                    kind=(kwargs.get("kind") or "").strip() or None,
                    status=(kwargs.get("status") or "").strip() or None,
                    source=(kwargs.get("source") or "").strip() or None,
                    unused_only=bool(kwargs.get("unused_only", False)),
                    limit=limit,
                )
                return ToolResult(
                    success=True,
                    data={"assets": assets, "count": len(assets)},
                    message=f"共 {len(assets)} 个进化资产",
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 列出失败: {e}")

        elif action == "get":
            asset_id = (kwargs.get("asset_id") or "").strip()
            if not asset_id:
                return ToolResult(success=False, data={}, message="get 需要提供 asset_id")
            try:
                a = store.get_asset(asset_id)
                if not a:
                    return ToolResult(success=False, data={}, message="资产不存在")
                return ToolResult(success=True, data=a, message=f"资产 `{a.get('name', asset_id)}`")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 获取失败: {e}")

        elif action == "tasks":
            try:
                get_evolution_manager().ensure_seeded()
                tasks = store.list_tasks()
                return ToolResult(
                    success=True,
                    data={"tasks": tasks, "count": len(tasks)},
                    message=f"共 {len(tasks)} 个演化任务",
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 列出失败: {e}")

        elif action == "clusters":
            try:
                get_evolution_manager().ensure_seeded()
                clusters = store.list_clusters(50)
                return ToolResult(
                    success=True,
                    data={"clusters": clusters, "count": len(clusters)},
                    message=f"共 {len(clusters)} 个聚类",
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 列出失败: {e}")

        elif action == "version":
            try:
                from backend.evolution.config import ENGINE_VERSION, get_evolution_config

                cfg = get_evolution_config()
                return ToolResult(
                    success=True,
                    data={
                        "engine_version": ENGINE_VERSION,
                        "phases": ["P1_tasks", "P2_skill_md", "P3_tool_draft", "P4_observe_curator"],
                        "enabled": cfg.enabled,
                    },
                    message=f"进化引擎版本 {ENGINE_VERSION}",
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 获取版本失败: {e}")

        return ToolResult(success=False, data={}, message=f"未知 action: {action}")


# ── Git 协作（只读） ──

class ManageGit(BaseTool):
    """Git 协作工具（复用 git 路由的 _resolve_repo_path/_run_git，不直接 subprocess）"""

    def __init__(self):
        super().__init__(
            name="manage_git",
            description=(
                "查看 Git 仓库状态与变更。action: status(分支/ahead/behind/改动文件概览)、"
                "branches(分支列表)、diff(未暂存+已暂存 diff，可按 file 过滤)、log(最近提交)。"
                "全部为只读操作，不提供 commit/push/reset 等写操作；工作区不是 git 仓库时返回不可用提示"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "branches", "diff", "log"],
                        "description": "操作类型",
                    },
                    "file": {"type": "string", "description": "diff 时: 可选，仅看指定文件的 diff（仓库内相对路径）"},
                    "limit": {"type": "integer", "description": "log 时: 提交条数，默认 20，最大 100"},
                },
                "required": ["action"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )

    async def execute(self, action: str, **kwargs: Any) -> ToolResult:
        # 复用 git 路由的仓库解析与命令执行（失败返回空串，不抛 500）
        from backend.api.routes.git import _resolve_repo_path, _run_git

        repo = _resolve_repo_path()
        if repo is None:
            return ToolResult(
                success=False,
                data={"is_repo": False, "reason": "no_git_repo"},
                message="当前工作区不是 git 仓库（也未配置 TAKTON_GIT_REPO），Git 功能不可用",
            )

        if action == "status":
            try:
                branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
            except FileNotFoundError:
                return ToolResult(success=False, data={"is_repo": False, "reason": "git_not_installed"}, message="系统未安装 git")
            if not branch:
                return ToolResult(
                    success=False,
                    data={"is_repo": False, "reason": "not_a_repo", "repo_path": str(repo)},
                    message=f"{repo} 不是有效的 git 仓库",
                )
            status_output = _run_git(["status", "--short"], repo)
            changed_files = []
            if status_output:
                for line in status_output.split("\n"):
                    if line.strip():
                        changed_files.append({"status": line[:2].strip(), "file": line[3:].strip()})
            ahead = behind = 0
            ahead_behind = _run_git(["rev-list", "--count", "--left-right", f"origin/{branch}...HEAD"], repo)
            if ahead_behind and "\t" in ahead_behind:
                parts = ahead_behind.split("\t")
                behind = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
                ahead = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
            total_commits = _run_git(["rev-list", "--count", "HEAD"], repo)
            data = {
                "branch": branch,
                "ahead": ahead,
                "behind": behind,
                "total_commits": int(total_commits) if total_commits and total_commits.isdigit() else 0,
                "changed_files": changed_files,
                "has_changes": len(changed_files) > 0,
                "is_dirty": any(f.get("status", "") not in ("", "??") for f in changed_files),
                "is_repo": True,
                "repo_path": str(repo),
            }
            return ToolResult(
                success=True,
                data=data,
                message=f"分支 `{branch}`，{len(changed_files)} 个改动文件，共 {data['total_commits']} 次提交",
            )

        elif action == "branches":
            try:
                output = _run_git(["branch", "--list"], repo)
            except FileNotFoundError:
                return ToolResult(success=False, data={"is_repo": False, "reason": "git_not_installed"}, message="系统未安装 git")
            branches = []
            if output:
                for line in output.split("\n"):
                    line = line.strip()
                    if line:
                        branches.append({"name": line.lstrip("* ").strip(), "current": line.startswith("* ")})
            return ToolResult(success=True, data={"branches": branches, "count": len(branches)}, message=f"共 {len(branches)} 个分支")

        elif action == "diff":
            file_arg = (kwargs.get("file") or "").strip()
            args = ["diff"]
            staged_args = ["diff", "--cached"]
            if file_arg:
                # 与路由一致：仅允许仓库内相对路径
                safe = file_arg.replace("\\", "/").lstrip("/")
                if ".." in safe.split("/"):
                    return ToolResult(success=False, data={}, message="非法文件路径")
                args.extend(["--", safe])
                staged_args.extend(["--", safe])
            try:
                diff_output = _run_git(args, repo)
                staged_output = _run_git(staged_args, repo)
            except FileNotFoundError:
                return ToolResult(success=False, data={"is_repo": False, "reason": "git_not_installed"}, message="系统未安装 git")
            data = {
                "unstaged": diff_output,
                "staged": staged_output,
                "has_changes": bool(diff_output or staged_output),
                "is_repo": True,
            }
            scope = f"文件 `{file_arg}`" if file_arg else "工作区"
            return ToolResult(
                success=True,
                data=data,
                message=f"{scope} {'有' if data['has_changes'] else '无'}变更",
            )

        elif action == "log":
            try:
                limit = int(kwargs.get("limit", 20) or 20)
            except (TypeError, ValueError):
                return ToolResult(success=False, data={}, message="limit 必须是整数")
            limit = max(1, min(limit, 100))
            try:
                output = _run_git(["log", "--oneline", "-n", str(limit)], repo)
            except FileNotFoundError:
                return ToolResult(success=False, data={"is_repo": False, "reason": "git_not_installed"}, message="系统未安装 git")
            commits = []
            if output:
                for line in output.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    sha, _, subject = line.partition(" ")
                    commits.append({"sha": sha, "subject": subject})
            return ToolResult(success=True, data={"commits": commits, "count": len(commits)}, message=f"最近 {len(commits)} 次提交")

        return ToolResult(success=False, data={}, message=f"未知 action: {action}")


# ── 自进化写操作 ──

class ManageEvolution(BaseTool):
    """自进化管理工具（对齐 evolution 路由的写端点，调用相同的 manager/store/skill_sync 函数）"""

    def __init__(self):
        super().__init__(
            name="manage_evolution",
            description=(
                "管理自进化（Evolution）系统。action: "
                "config(启用/禁用进化引擎及开关)、"
                "enable_asset(启用资产并同步为技能)/disable_asset(禁用资产)、"
                "apply_draft(草稿过安全门后转正)/reject_draft(弃用草稿)、"
                "delete_asset(删除单个资产，预置 seed 不可删)、"
                "bulk_delete_unused(清理未使用的 auto 资产)、"
                "run_task(运行指定演化任务)、"
                "curator_run(运行聚类整理器，建议先 dry_run=true 预览)"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "config", "enable_asset", "disable_asset",
                            "apply_draft", "reject_draft", "delete_asset",
                            "bulk_delete_unused", "run_task", "curator_run",
                        ],
                        "description": "操作类型",
                    },
                    "asset_id": {"type": "string", "description": "enable_asset/disable_asset/apply_draft/reject_draft/delete_asset 时: 资产 ID"},
                    "enabled": {"type": "boolean", "description": "config 时: 是否启用进化引擎"},
                    "auto_apply_skills": {"type": "boolean", "description": "config 时: 自动应用演化技能"},
                    "auto_apply_tools": {"type": "boolean", "description": "config 时: 自动应用演化工具"},
                    "auto_observe": {"type": "boolean", "description": "config 时: 自动观察"},
                    "auto_create_tools": {"type": "boolean", "description": "config 时: 自动创建工具"},
                    "curator_enabled": {"type": "boolean", "description": "config 时: 启用整理器"},
                    "mode": {"type": "string", "description": "config 时: 运行模式"},
                    "task_name": {"type": "string", "description": "run_task 时: 演化任务名"},
                    "dry_run": {"type": "boolean", "description": "curator_run 时: 仅预览不落地，默认 true（安全）"},
                },
                "required": ["action"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.MEDIUM,
        )

    async def _sync_skill(self, asset: dict[str, Any], *, enabled: bool) -> None:
        """与路由一致：资产状态变更后同步技能表（失败不阻断）"""
        try:
            from backend.evolution.skill_sync import upsert_skill_from_asset

            await upsert_skill_from_asset(
                name=asset["name"],
                summary=asset.get("summary") or asset["name"],
                content=asset.get("content") or "",
                asset_id=asset.get("id"),
                kind=asset.get("kind") or "skill",
                enabled=enabled,
            )
        except Exception as e:
            logger.warning("evolution skill sync failed: %s", e)

    async def execute(self, action: str, **kwargs: Any) -> ToolResult:
        try:
            from backend.evolution import store
            from backend.evolution.manager import get_evolution_manager
        except Exception as e:
            return ToolResult(success=False, data={}, message=f"❌ 进化模块不可用: {e}")

        if action == "config":
            try:
                from backend.evolution.config import set_evolution_config

                cfg_kwargs: dict[str, Any] = {}
                for key in ("enabled", "auto_apply_skills", "auto_apply_tools",
                            "auto_observe", "auto_create_tools", "curator_enabled"):
                    if kwargs.get(key) is not None:
                        cfg_kwargs[key] = bool(kwargs[key])
                if kwargs.get("mode") is not None:
                    cfg_kwargs["mode"] = str(kwargs["mode"])
                if kwargs.get("from_cron") is not None:
                    cfg_kwargs["from_cron"] = bool(kwargs["from_cron"])
                if kwargs.get("from_tasks") is not None:
                    cfg_kwargs["from_tasks"] = bool(kwargs["from_tasks"])
                if not cfg_kwargs:
                    return ToolResult(success=False, data={}, message="config 至少需要提供一项配置（如 enabled）")
                set_evolution_config(**cfg_kwargs)
                mgr = get_evolution_manager()
                return ToolResult(success=True, data=mgr.status(), message=f"✅ 进化配置已更新: {sorted(cfg_kwargs.keys())}")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 配置失败: {e}")

        elif action in ("enable_asset", "disable_asset"):
            asset_id = (kwargs.get("asset_id") or "").strip()
            if not asset_id:
                return ToolResult(success=False, data={}, message=f"{action} 需要提供 asset_id")
            try:
                new_status = "active" if action == "enable_asset" else "disabled"
                a = store.update_asset_status(asset_id, new_status)
                if not a:
                    return ToolResult(success=False, data={}, message="资产不存在")
                await self._sync_skill(a, enabled=(action == "enable_asset"))
                return ToolResult(success=True, data=a, message=f"✅ 资产 `{a.get('name', asset_id)}` 已{'启用' if action == 'enable_asset' else '禁用'}")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 操作失败: {e}")

        elif action == "apply_draft":
            asset_id = (kwargs.get("asset_id") or "").strip()
            if not asset_id:
                return ToolResult(success=False, data={}, message="apply_draft 需要提供 asset_id")
            try:
                a = store.get_asset(asset_id)
                if not a:
                    return ToolResult(success=False, data={}, message="资产不存在")
                from backend.evolution.gates import run_gates

                gate = run_gates(
                    name=a["name"],
                    content=a.get("content") or "",
                    summary=a.get("summary") or "",
                    score=a.get("last_score"),
                    baseline_score=0.5,
                )
                if not gate["ok"]:
                    return ToolResult(success=False, data={"gates": gate}, message="❌ 未过安全门，草稿未转正")
                updated = store.update_asset_status(asset_id, "active")
                if updated:
                    await self._sync_skill(updated, enabled=True)
                return ToolResult(success=True, data={"asset": updated, "gate": gate}, message=f"✅ 草稿 `{a.get('name', asset_id)}` 已转正")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 转正失败: {e}")

        elif action == "reject_draft":
            asset_id = (kwargs.get("asset_id") or "").strip()
            if not asset_id:
                return ToolResult(success=False, data={}, message="reject_draft 需要提供 asset_id")
            try:
                a = store.get_asset(asset_id)
                if not a:
                    return ToolResult(success=False, data={}, message="资产不存在")
                updated = store.update_asset_status(asset_id, "rejected")
                try:
                    from backend.evolution.skill_sync import delete_skill_by_name

                    await delete_skill_by_name(a["name"], only_evolved=True)
                except Exception as e:
                    logger.warning("evolution reject skill cleanup failed: %s", e)
                return ToolResult(success=True, data=updated or {}, message=f"✅ 草稿 `{a.get('name', asset_id)}` 已弃用")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 弃用失败: {e}")

        elif action == "delete_asset":
            asset_id = (kwargs.get("asset_id") or "").strip()
            if not asset_id:
                return ToolResult(success=False, data={}, message="delete_asset 需要提供 asset_id")
            try:
                a = store.get_asset(asset_id)
                if not a:
                    return ToolResult(success=False, data={}, message="资产不存在")
                if a.get("source") == "seed":
                    return ToolResult(success=False, data={}, message="预置（seed）资产不可删除")
                ok = store.delete_asset(asset_id)
                if not ok:
                    return ToolResult(success=False, data={}, message="删除失败")
                try:
                    from backend.evolution.skill_sync import purge_asset

                    await purge_asset(a)
                except Exception as e:
                    logger.warning("evolution purge skill failed: %s", e)
                return ToolResult(success=True, data={"id": asset_id, "name": a.get("name")}, message=f"✅ 资产 `{a.get('name', asset_id)}` 已删除")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 删除失败: {e}")

        elif action == "bulk_delete_unused":
            try:
                to_purge = [a for a in store.list_assets(source="auto", unused_only=True, limit=500) if a.get("source") != "seed"]
                store.bulk_delete_unused_auto()
                try:
                    from backend.evolution.skill_sync import purge_asset

                    for a in to_purge:
                        try:
                            await purge_asset(a)
                        except Exception:
                            pass
                except Exception as e:
                    logger.warning("evolution bulk purge failed: %s", e)
                names = [a.get("name") for a in to_purge if a.get("name")]
                return ToolResult(
                    success=True,
                    data={"deleted": len(to_purge), "skill_names": names},
                    message=f"✅ 已清理 {len(to_purge)} 个未使用的 auto 资产",
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 清理失败: {e}")

        elif action == "run_task":
            task_name = (kwargs.get("task_name") or "").strip()
            if not task_name:
                return ToolResult(success=False, data={}, message="run_task 需要提供 task_name")
            try:
                mgr = get_evolution_manager()
                res = await mgr.run_task(task_name, improve=True)
                return ToolResult(success=True, data=res or {}, message=f"✅ 演化任务 `{task_name}` 已执行")
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 任务执行失败: {e}")

        elif action == "curator_run":
            dry_run = bool(kwargs.get("dry_run", True))
            try:
                mgr = get_evolution_manager()
                res = await mgr.run_curator(dry_run=dry_run)
                return ToolResult(
                    success=True,
                    data=res or {},
                    message=f"✅ 整理器已运行（{'预览模式，未落地' if dry_run else '已落地'}）",
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 整理器运行失败: {e}")

        return ToolResult(success=False, data={}, message=f"未知 action: {action}")
