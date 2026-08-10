"""
MCP 工具适配器

将 MCP Server 返回的工具定义包装成 BaseTool，接入统一 ToolRegistry。
"""

from __future__ import annotations

import logging
import re
from typing import Any

from backend.mcp_hub.client import MCPClient, MCPClientManager
from backend.tools.base import BaseTool, ToolRiskLevel, ToolSource

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"[^a-zA-Z0-9_]+")


def _sanitize_name_part(raw: str) -> str:
    s = _SAFE_NAME.sub("_", (raw or "").strip()).strip("_")
    return s or "tool"


def mcp_registry_name(server_name: str, tool_name: str, *, prefer_flat: bool = True) -> str:
    """生成 Registry 内工具名。

    默认 ``mcp_{tool}``（兼容既有测试/调用习惯）；
    若同名已被其他 server 占用，则用 ``mcp_{server}_{tool}``。
    """
    remote = tool_name or "tool"
    if remote.startswith("mcp_"):
        flat = remote
    else:
        flat = f"mcp_{_sanitize_name_part(remote)}"
    if not prefer_flat:
        return f"mcp_{_sanitize_name_part(server_name)}_{_sanitize_name_part(remote)}"
    return flat


class MCPToolAdapter(BaseTool):
    """单个 MCP 工具的 BaseTool 包装"""

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        description: str,
        parameters: dict[str, Any],
        client: MCPClient | None = None,
        client_manager: MCPClientManager | None = None,
        risk_level: str | ToolRiskLevel | None = None,
        registry_name: str | None = None,
    ):
        self.server_name = server_name
        # 远程 server 只认原始 tool name，不认本地前缀
        self.remote_name = tool_name
        # 不长期持有 client：sync 后会 close_all 换新连接，执行时经 manager 查找
        self._client = None  # kept for tests that inject a fixed client
        self._injected_client = client
        self._client_manager = client_manager

        prefixed_name = registry_name or mcp_registry_name(server_name, tool_name)
        # 默认低风险：避免 confirm 弹窗把 MCP 搜索挡住
        risk = ToolRiskLevel.map(
            risk_level.value if isinstance(risk_level, ToolRiskLevel) else (risk_level or "low")
        )
        if risk in (ToolRiskLevel.MEDIUM, ToolRiskLevel.HIGH, ToolRiskLevel.DANGEROUS):
            # 仍允许服务器配置提高风险，但默认不因 medium 触发确认链
            pass

        desc = description or f"MCP tool {tool_name} from server {server_name}"
        if server_name and f"[{server_name}]" not in desc:
            desc = f"[{server_name}] {desc}"

        super().__init__(
            name=prefixed_name,
            description=desc,
            parameters=parameters or {"type": "object", "properties": {}},
            source=ToolSource.MCP,
            risk_level=risk if risk_level else ToolRiskLevel.LOW,
            enabled=True,
            requires_confirmation=False,
        )

    def _get_client(self) -> MCPClient | None:
        # 注入的 client（单测）优先；生产路径始终走 manager 拿当前连接
        if self._injected_client is not None:
            if getattr(self._injected_client, "_initialized", False):
                return self._injected_client
        if self._client_manager is None:
            from backend.mcp_hub.client import get_mcp_manager

            self._client_manager = get_mcp_manager()
        return self._client_manager.get_client(self.server_name)

    async def execute(self, **kwargs) -> Any:
        # 已挂载 MCP：不二次 mediate；断连则单服重连（不对全表 close_all）。
        client = self._get_client()
        if client is None or not getattr(client, "_initialized", False):
            try:
                from backend.mcp_hub.service import sync_mcp_runtime

                await sync_mcp_runtime(only_server=self.server_name)
                client = self._get_client()
            except Exception as e:
                logger.warning("MCP reconnect %s failed: %s", self.server_name, e)
        if client is None or not getattr(client, "_initialized", False):
            return (
                f"[Error] MCP server '{self.server_name}' not connected。"
                "请确认已启用且包名/环境变量正确，或 POST /api/mcp/reload。"
            )

        clean = {
            k: v
            for k, v in kwargs.items()
            if not str(k).startswith("_")
            and str(k) not in ("ws_manager", "connection_manager", "user_id")
            and "ConnectionManager" not in type(v).__name__
        }
        result = await client.call_tool(self.remote_name, clean)
        # 半死连接：再试一次单服重连
        if isinstance(result, str) and result.startswith("[Error] MCP tool") and (
            "not connected" in result.lower()
            or "closed" in result.lower()
            or "cancel scope" in result.lower()
        ):
            try:
                from backend.mcp_hub.service import sync_mcp_runtime

                await sync_mcp_runtime(only_server=self.server_name)
                client = self._get_client()
                if client is not None:
                    result = await client.call_tool(self.remote_name, clean)
            except Exception:
                pass
        return result


def unregister_all_mcp_tools(registry=None) -> int:
    """移除 ToolRegistry 中全部 MCP 来源工具，返回移除数量。"""
    from backend.tools.registry import ToolRegistry

    target = registry or ToolRegistry
    names = [t.name for t in target.get_all(source=ToolSource.MCP)]
    for n in names:
        target.unregister(n)
    return len(names)


def unregister_mcp_server_tools(server_name: str, registry=None) -> int:
    """移除指定 server 注册的 MCP 工具。"""
    from backend.tools.registry import ToolRegistry

    target = registry or ToolRegistry
    removed = 0
    for t in list(target.get_all(source=ToolSource.MCP)):
        if getattr(t, "server_name", None) == server_name:
            target.unregister(t.name)
            removed += 1
    return removed


async def register_mcp_server_tools(
    server_name: str,
    client: MCPClient,
    registry=None,
    risk_level: str | None = None,
    tools_include: list[str] | None = None,
    tools_exclude: list[str] | None = None,
) -> int:
    """从 MCP Server 拉取工具列表并注册到统一 ToolRegistry。

    tools_include / tools_exclude：Hermes 风格白名单（原始工具名，支持 fnmatch）。
    include 非空仅挂匹配项；include 空则挂全部再减 exclude。
    """
    from backend.mcp_hub.normalize import tool_name_allowed
    from backend.tools.registry import ToolRegistry

    target_registry = registry or ToolRegistry

    tools = await client.list_tools()
    registered = 0
    skipped = 0
    for tool in tools.tools:
        remote = tool.name
        if not tool_name_allowed(remote, tools_include, tools_exclude):
            skipped += 1
            continue
        flat = mcp_registry_name(server_name, remote, prefer_flat=True)
        existing = target_registry.get(flat)
        # 同名冲突且来自不同 server → 改用 server 命名空间
        if (
            existing is not None
            and getattr(existing, "source", None) == ToolSource.MCP
            and getattr(existing, "server_name", None) not in (None, server_name)
        ):
            reg_name = mcp_registry_name(server_name, remote, prefer_flat=False)
        else:
            reg_name = flat

        # SDK 字段名因版本而异：input_schema (pydantic v2) / inputSchema (旧 camelCase)
        raw_schema = (
            getattr(tool, "inputSchema", None)
            or getattr(tool, "input_schema", None)
            or {}
        )
        if hasattr(raw_schema, "model_dump"):
            raw_schema = raw_schema.model_dump(by_alias=True, exclude_none=True)
        elif hasattr(raw_schema, "dict"):
            raw_schema = raw_schema.dict()
        if not isinstance(raw_schema, dict):
            raw_schema = {"type": "object", "properties": {}}

        adapter = MCPToolAdapter(
            server_name=server_name,
            tool_name=remote,
            description=tool.description or "",
            parameters=raw_schema or {"type": "object", "properties": {}},
            # 生产不 pin client：sync 后经 manager 取当前连接
            client=None,
            risk_level=risk_level,
            registry_name=reg_name,
        )
        # 仅当 client 不在全局 manager 时注入（单测 fake manager）
        try:
            from backend.mcp_hub.client import get_mcp_manager

            live = get_mcp_manager().get_client(server_name)
            if live is not client:
                adapter._injected_client = client
        except Exception:
            adapter._injected_client = client
        target_registry.register(adapter)
        registered += 1
    if skipped:
        logger.info(
            "MCP server '%s': registered=%s skipped_by_policy=%s include=%s exclude=%s",
            server_name,
            registered,
            skipped,
            tools_include,
            tools_exclude,
        )
    return registered
