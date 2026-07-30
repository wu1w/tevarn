"""Integration management tools: mcp, channel, webhook."""
from __future__ import annotations

import logging
import uuid as uuid_mod
from typing import Any

from backend.tools.base import BaseTool, ToolRiskLevel, ToolSource
from backend.tools.builtins.manage_common import _iso, _parse_uuid
from backend.tools.builtins.self_config import ToolResult

logger = logging.getLogger(__name__)


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

