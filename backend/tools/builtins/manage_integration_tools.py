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
                "管理 MCP Server 配置（安装/改 env/热挂载）。"
                "action: list/get/add/update/delete/tools/set_tools。"
                "用户给了 API Key/密钥要配 MCP 时：先 list 看 name，再 update name=<server> "
                "env={KEY:value}（如 ASK_ECHO_SEARCH_INFINITY_API_KEY / TAVILY_API_KEY），"
                "勿用 web_search/mcp_* 去「调研怎么配」。update 后会热同步；再用 mcp_* 自测。"
                "add 需要 name+transport（stdio|sse）；stdio 需 command（tavily/doubao-search 等可省略走默认模板），sse 需 url。"
                "tools: 列远端工具与白名单；set_tools: tools_include/exclude（原始工具名，支持 *）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "get", "add", "update", "delete", "tools", "set_tools"],
                        "description": "操作类型",
                    },
                    "server_id": {"type": "string", "description": "get/update/delete/tools/set_tools 时: MCP Server UUID（与 name 二选一）"},
                    "name": {"type": "string", "description": "add: 服务名称；其他 action 可按名称定位"},
                    "transport": {"type": "string", "description": "add/update: 传输方式 stdio | sse"},
                    "command": {"type": "string", "description": "add/update: stdio 启动命令"},
                    "args": {"type": "array", "items": {"type": "string"}, "description": "add/update: 启动参数"},
                    "url": {"type": "string", "description": "add/update: sse 服务地址"},
                    "env": {
                        "type": "object",
                        "description": (
                            "add/update: 环境变量 dict。"
                            "填 API Key 时写真实键名，如 "
                            "{\"ASK_ECHO_SEARCH_INFINITY_API_KEY\":\"...\"} 或 "
                            "{\"TAVILY_API_KEY\":\"...\"}；空值不会覆盖已有密钥。"
                        ),
                    },
                    "enabled": {"type": "boolean", "description": "add/update: 是否启用"},
                    "timeout": {"type": "number", "description": "add/update: 超时秒数，默认 30"},
                    "risk_level": {"type": "string", "description": "add/update: 风险等级，默认 medium"},
                    "allowed_paths": {"type": "array", "items": {"type": "string"}, "description": "add/update: 允许访问的路径"},
                    "tools_include": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "set_tools: 白名单原始工具名（非空则只挂这些；支持 * 通配）",
                    },
                    "tools_exclude": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "set_tools: 黑名单（仅当 tools_include 为空时生效）",
                    },
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
            "tools_include": getattr(obj, "tools_include", None),
            "tools_exclude": getattr(obj, "tools_exclude", None),
        }

    def _format_server_line(self, d: dict[str, Any], *, connected: bool | None = None) -> str:
        """给人/模型可读的单行摘要（ToolResult 只暴露 message，必须写进 message）。"""
        bits = [
            f"name=`{d.get('name')}`",
            f"id={d.get('id')}",
            f"enabled={d.get('enabled')}",
            f"transport={d.get('transport')}",
        ]
        if d.get("command"):
            args = d.get("args") or []
            bits.append(f"cmd=`{d.get('command')} {' '.join(str(a) for a in args[:4])}`".strip())
        if connected is not None:
            bits.append(f"connected={connected}")
        inc = d.get("tools_include")
        if inc:
            bits.append(f"tools_include={inc}")
        return " · ".join(bits)

    async def _resolve_id(self, repo: Any, kwargs: dict[str, Any]) -> uuid_mod.UUID:
        server_id = (kwargs.get("server_id") or "").strip()
        if server_id:
            try:
                uid = _parse_uuid(server_id, "server_id")
                obj = await repo.get_by_id(uid)
                if obj is not None:
                    return obj.id
                # 合法 UUID 但不在库里：模型编造的 id，继续走 name/唯一 server
            except ValueError:
                pass
        name = (kwargs.get("name") or kwargs.get("server_name") or "").strip()
        if name:
            obj = await repo.get_by_name(name)
            if obj is None:
                all_s = await repo.list_all()
                for s in all_s:
                    if str(s.name).lower() == name.lower():
                        return s.id
                raise ValueError(f"MCP Server 不存在: {name}")
            return obj.id
        # 唯一 server 时自动选用（list 的 message 过去没带 id，模型会瞎填 UUID）
        all_s = await repo.list_all()
        if len(all_s) == 1:
            return all_s[0].id
        if not all_s:
            raise ValueError("没有任何 MCP Server，请先 add 或从商店安装")
        names = ", ".join(f"`{s.name}`(id={s.id})" for s in all_s[:12])
        raise ValueError(f"需要提供 server_id 或 name。现有: {names}")

    async def execute(self, action: str, **kwargs: Any) -> ToolResult:
        from backend.repositories.mcp_server_repo import AsyncMCPServerRepository
        from backend.schemas.mcp import MCPServerCreate, MCPServerUpdate

        repo = AsyncMCPServerRepository()

        if action == "list":
            try:
                servers = await repo.list_all()
                data = [self._to_dict(s) for s in servers]
                # 附带连接状态
                status_map: dict[str, bool] = {}
                try:
                    from backend.mcp_hub.service import get_mcp_status

                    for st in await get_mcp_status():
                        status_map[str(st.get("name") or "")] = bool(st.get("connected"))
                except Exception:
                    pass
                lines = [f"共 {len(data)} 个 MCP Server："]
                if not data:
                    lines.append("（空）请 manage_mcp add 或商店安装。")
                for d in data:
                    conn = status_map.get(str(d.get("name") or ""))
                    lines.append(f"- {self._format_server_line(d, connected=conn)}")
                lines.append(
                    "下一步: tools name=<上列 name> 查看工具；"
                    "直接调用 mcp_<tool> 如 mcp_tavily_search；"
                    "set_tools 可设 tools_include 白名单。"
                )
                return ToolResult(
                    success=True,
                    data={"servers": data, "count": len(data)},
                    message="\n".join(lines),
                )
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 列出失败: {e}")

        elif action == "get":
            try:
                sid = await self._resolve_id(repo, kwargs)
                obj = await repo.get_by_id(sid)
                if obj is None:
                    return ToolResult(success=False, data={}, message="MCP Server 不存在")
                d = self._to_dict(obj)
                return ToolResult(
                    success=True,
                    data=d,
                    message=f"MCP Server {self._format_server_line(d)}",
                )
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
            _known_stdio = (
                "tavily",
                "firecrawl",
                "doubao-search",
                "doubao",
                "askecho",
                "askecho-search",
                "mcp-server-askecho-search-infinity",
            )
            if transport == "stdio" and not (kwargs.get("command") or "").strip() and name.lower() not in _known_stdio:
                return ToolResult(success=False, data={}, message="stdio 传输需要提供 command")
            if transport == "sse" and not (kwargs.get("url") or "").strip():
                return ToolResult(success=False, data={}, message="sse 传输需要提供 url")
            try:
                if await repo.get_by_name(name):
                    return ToolResult(success=False, data={}, message=f"MCP Server `{name}` 已存在")
                from backend.mcp_hub.normalize import normalize_server_fields

                norm = normalize_server_fields(
                    name=name,
                    command=kwargs.get("command") or ("npx" if transport == "stdio" else None),
                    args=list(kwargs.get("args") or []),
                    env=kwargs.get("env") or {},
                    existing_env={},
                )
                data = MCPServerCreate(
                    name=name,
                    transport=transport,
                    command=norm["command"],
                    args=norm["args"],
                    url=kwargs.get("url") or None,
                    env=norm["env"],
                    enabled=bool(kwargs.get("enabled", True)),
                    timeout=float(kwargs.get("timeout", 30.0)),
                    risk_level=str(kwargs.get("risk_level") or "low"),
                    allowed_paths=kwargs.get("allowed_paths"),
                )
                obj = await repo.create(data)
                rt = await self._sync_runtime(only_server=name)
                d = self._to_dict(obj)
                d["runtime"] = rt
                ok_rt = bool(rt.get("ok")) and name in (rt.get("connected") or [])
                msg = f"✅ MCP Server `{name}` 已添加"
                if ok_rt:
                    msg += f"并热挂载（{rt.get('registered', 0)} tools）"
                else:
                    msg += f"（DB 已写，运行时: {rt.get('error') or rt.get('warning') or 'not connected'}）"
                return ToolResult(success=True, data=d, message=msg)
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 添加失败: {e}")

        elif action == "update":
            try:
                sid = await self._resolve_id(repo, kwargs)
                existing = await repo.get_by_id(sid)
                if existing is None:
                    return ToolResult(success=False, data={}, message="MCP Server 不存在")
                from backend.mcp_hub.normalize import normalize_server_fields

                patch: dict[str, Any] = {}
                if kwargs.get("name") is not None:
                    patch["name"] = str(kwargs["name"]).strip()
                if kwargs.get("transport") is not None:
                    t = str(kwargs["transport"]).strip()
                    if t not in ("stdio", "sse"):
                        return ToolResult(success=False, data={}, message="transport 必须是 stdio 或 sse")
                    patch["transport"] = t
                # command/args/env 统一规范化：纠正错包、合并密钥、拒绝脱敏覆盖
                want_cmd = kwargs.get("command") if kwargs.get("command") is not None else existing.command
                want_args = kwargs.get("args") if kwargs.get("args") is not None else (existing.args or [])
                want_env = kwargs.get("env") if kwargs.get("env") is not None else None
                sname = str(patch.get("name") or existing.name)
                norm = normalize_server_fields(
                    name=sname,
                    command=want_cmd,
                    args=list(want_args or []),
                    env=want_env if want_env is not None else {},
                    existing_env=existing.env or {},
                )
                # 只要涉及 stdio 字段或 name=tavily/doubao 类，就写回规范化结果
                _known_stdio = (
                    "tavily",
                    "firecrawl",
                    "doubao-search",
                    "doubao",
                    "askecho",
                    "askecho-search",
                    "mcp-server-askecho-search-infinity",
                )
                if (
                    kwargs.get("command") is not None
                    or kwargs.get("args") is not None
                    or kwargs.get("env") is not None
                    or sname.lower() in _known_stdio
                ):
                    patch["command"] = norm["command"]
                    patch["args"] = norm["args"]
                    patch["env"] = norm["env"]
                if kwargs.get("url") is not None:
                    patch["url"] = str(kwargs["url"])
                if kwargs.get("enabled") is not None:
                    patch["enabled"] = bool(kwargs["enabled"])
                if kwargs.get("timeout") is not None:
                    patch["timeout"] = float(kwargs["timeout"])
                if kwargs.get("risk_level") is not None:
                    patch["risk_level"] = str(kwargs["risk_level"])
                if kwargs.get("allowed_paths") is not None:
                    patch["allowed_paths"] = kwargs["allowed_paths"]
                if kwargs.get("tools_include") is not None:
                    from backend.mcp_hub.normalize import normalize_tool_name_list

                    patch["tools_include"] = normalize_tool_name_list(kwargs.get("tools_include"))
                if kwargs.get("tools_exclude") is not None:
                    from backend.mcp_hub.normalize import normalize_tool_name_list

                    patch["tools_exclude"] = normalize_tool_name_list(kwargs.get("tools_exclude"))
                if not patch:
                    return ToolResult(success=False, data={}, message="update 至少需要提供一项更新")
                obj = await repo.update(sid, MCPServerUpdate(**patch))
                if obj is None:
                    return ToolResult(success=False, data={}, message="MCP Server 不存在")
                rt = await self._sync_runtime(only_server=obj.name)
                d = self._to_dict(obj)
                d["runtime"] = rt
                ok_rt = bool(rt.get("ok")) and obj.name in (rt.get("connected") or [])
                msg = f"✅ MCP Server `{obj.name}` 已更新"
                if ok_rt:
                    msg += f"并热同步（connected, {rt.get('registered', 0)} tools）"
                else:
                    msg += f"（DB 已写，运行时: {rt.get('error') or rt.get('warning') or 'not connected'}）"
                return ToolResult(success=True, data=d, message=msg)
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 更新失败: {e}")

        elif action == "tools":
            # 列出远端工具 + 白名单选中状态
            try:
                sid = await self._resolve_id(repo, kwargs)
                obj = await repo.get_by_id(sid)
                if obj is None:
                    return ToolResult(success=False, data={}, message="MCP Server 不存在")
                from backend.mcp_hub.client import get_mcp_manager
                from backend.mcp_hub.normalize import tool_name_allowed
                from backend.tools.adapters.mcp_adapter import mcp_registry_name

                manager = get_mcp_manager()
                client = manager.get_client(obj.name)
                if client is None or not getattr(client, "_initialized", False):
                    await self._sync_runtime(only_server=obj.name)
                    client = manager.get_client(obj.name)
                if client is None or not getattr(client, "_initialized", False):
                    return ToolResult(
                        success=False,
                        data=self._to_dict(obj),
                        message=f"MCP Server `{obj.name}` 未连接，无法列出工具",
                    )
                remote = await client.list_tools()
                inc = getattr(obj, "tools_include", None)
                exc = getattr(obj, "tools_exclude", None)
                items = []
                for t in remote.tools:
                    items.append(
                        {
                            "name": t.name,
                            "description": (t.description or "")[:300],
                            "selected": tool_name_allowed(t.name, inc, exc),
                            "registry_name": mcp_registry_name(obj.name, t.name),
                        }
                    )
                selected = [x["name"] for x in items if x["selected"]]
                lines = [
                    f"MCP name=`{obj.name}` id={obj.id} 远端工具 {len(items)} 个，"
                    f"已挂载/白名单 {len(selected)} 个"
                    + (f"（include={inc}）" if inc else "（全量）")
                    + "：",
                ]
                for x in items:
                    mark = "✓" if x["selected"] else "·"
                    lines.append(
                        f"  {mark} remote=`{x['name']}` → call `{x['registry_name']}`"
                        + (f" — {x['description'][:80]}" if x.get("description") else "")
                    )
                if selected:
                    lines.append(
                        "可直接调用: " + ", ".join(f"`mcp_{n}`" if not n.startswith("mcp_") else f"`{n}`" for n in selected[:8])
                    )
                    # 更准确：用 registry_name
                    lines[-1] = "可直接调用: " + ", ".join(
                        f"`{x['registry_name']}`" for x in items if x["selected"]
                    )[:500]
                return ToolResult(
                    success=True,
                    data={
                        "server": self._to_dict(obj),
                        "tools": items,
                        "selected": selected,
                        "count": len(items),
                        "selected_count": len(selected),
                    },
                    message="\n".join(lines),
                )
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 列出工具失败: {e}")

        elif action == "set_tools":
            try:
                sid = await self._resolve_id(repo, kwargs)
                from backend.mcp_hub.normalize import normalize_tool_name_list

                patch: dict[str, Any] = {}
                if "tools_include" in kwargs:
                    patch["tools_include"] = normalize_tool_name_list(kwargs.get("tools_include"))
                if "tools_exclude" in kwargs:
                    patch["tools_exclude"] = normalize_tool_name_list(kwargs.get("tools_exclude"))
                if not patch:
                    return ToolResult(
                        success=False,
                        data={},
                        message="set_tools 需要 tools_include 和/或 tools_exclude",
                    )
                obj = await repo.update(sid, MCPServerUpdate(**patch))
                if obj is None:
                    return ToolResult(success=False, data={}, message="MCP Server 不存在")
                rt = await self._sync_runtime(only_server=obj.name)
                d = self._to_dict(obj)
                d["runtime"] = rt
                return ToolResult(
                    success=True,
                    data=d,
                    message=(
                        f"✅ MCP `{obj.name}` 工具白名单已更新"
                        f"（include={d.get('tools_include')} exclude={d.get('tools_exclude')}；"
                        f"registered={rt.get('registered', 0)}）"
                    ),
                )
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ set_tools 失败: {e}")

        elif action == "delete":
            try:
                sid = await self._resolve_id(repo, kwargs)
                ok = await repo.delete(sid)
                if not ok:
                    return ToolResult(success=False, data={}, message="MCP Server 不存在")
                rt = await self._sync_runtime()
                ok_rt = bool(rt.get("ok"))
                msg = f"✅ MCP Server `{sid}` 已删除"
                msg += "并卸载工具" if ok_rt else f"（DB 已删，运行时同步失败: {rt.get('error') or 'unknown'}）"
                return ToolResult(
                    success=True,
                    data={"server_id": str(sid), "runtime": rt},
                    message=msg,
                )
            except ValueError as e:
                return ToolResult(success=False, data={}, message=str(e))
            except Exception as e:
                return ToolResult(success=False, data={}, message=f"❌ 删除失败: {e}")

        return ToolResult(success=False, data={}, message=f"未知 action: {action}")

    async def _sync_runtime(self, only_server: str | None = None) -> dict:
        """DB 变更后热同步；可只重连单 server，避免全局 close_all 卸光工具。"""
        try:
            from backend.mcp_hub.service import sync_mcp_runtime

            return await sync_mcp_runtime(only_server=only_server)
        except Exception as e:
            logger.warning("manage_mcp runtime sync failed: %s", e)
            return {"ok": False, "error": str(e), "connected": []}


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

