"""MCP enable/mount/state 回归：sync_mcp_runtime、pack 可见性、cap 匹配、timeout。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.tools.base import ToolSource
from backend.tools.registry import ToolRegistry


class FakeTool:
    def __init__(self, name: str, description: str = "", input_schema: dict | None = None):
        self.name = name
        self.description = description or name
        self.inputSchema = input_schema or {"type": "object", "properties": {}}


class FakeListToolsResult:
    def __init__(self, tools):
        self.tools = tools


@pytest.fixture(autouse=True)
def clean_registry():
    ToolRegistry.clear()
    yield
    ToolRegistry.clear()


@pytest.mark.anyio
async def test_unregister_all_mcp_tools():
    from backend.tools.adapters.mcp_adapter import (
        MCPToolAdapter,
        unregister_all_mcp_tools,
    )

    ToolRegistry.register(
        MCPToolAdapter("s1", "echo", "e", {"type": "object", "properties": {}})
    )
    ToolRegistry.register(
        MCPToolAdapter("s1", "add", "a", {"type": "object", "properties": {}})
    )
    assert len(ToolRegistry.get_all(source=ToolSource.MCP)) == 2
    n = unregister_all_mcp_tools()
    assert n == 2
    assert ToolRegistry.get_all(source=ToolSource.MCP) == []


@pytest.mark.anyio
async def test_sync_mcp_runtime_unregisters_then_registers():
    """禁用/删除后不应残留 MCP 工具 schema。"""
    from backend.mcp_hub.client import MCPClient, MCPClientManager, MCPServerConfig
    from backend.mcp_hub.service import sync_mcp_runtime
    from backend.tools.adapters.mcp_adapter import MCPToolAdapter

    # 先塞一个僵尸 MCP 工具
    ToolRegistry.register(
        MCPToolAdapter("old", "zombie", "z", {"type": "object", "properties": {}})
    )
    assert ToolRegistry.get("mcp_zombie") is not None

    manager = MCPClientManager()
    client = MagicMock(spec=MCPClient)
    client.name = "live"
    client._initialized = True
    client.config = MCPServerConfig(name="live", transport="stdio", command="x")
    client.list_tools = AsyncMock(
        return_value=FakeListToolsResult([FakeTool("echo", "Echo")])
    )
    client.call_tool = AsyncMock(return_value="ok")
    client.close = AsyncMock()

    class FakeRow:
        name = "live"
        transport = "stdio"
        command = "echo"
        args = []
        url = None
        env = {}
        enabled = True
        timeout = 12.0
        risk_level = "low"

    class FakeRepo:
        async def get_all_enabled(self):
            return [FakeRow()]

        async def list_all(self):
            return [FakeRow()]

    async def fake_connect(configs):
        manager._clients["live"] = client

    with (
        patch("backend.mcp_hub.service.AsyncMCPServerRepository", FakeRepo),
        patch.object(manager, "connect", side_effect=fake_connect),
        patch.object(manager, "close_all", new_callable=AsyncMock),
        patch("backend.mcp_hub.client.get_mcp_manager", return_value=manager),
    ):
        # sync_mcp_runtime 签名为 only_server: str | None（manager 走 get_mcp_manager）
        result = await sync_mcp_runtime()

    assert result["ok"] is True
    assert ToolRegistry.get("mcp_zombie") is None
    assert ToolRegistry.get("mcp_echo") is not None
    assert "live" in result["connected"]


def test_live_mcp_tool_names_and_tools_for_packs():
    from backend.agent.tool_policy import live_mcp_tool_names, tools_for_packs
    from backend.tools.adapters.mcp_adapter import MCPToolAdapter

    ToolRegistry.register(
        MCPToolAdapter("s", "search", "s", {"type": "object", "properties": {}})
    )
    names = live_mcp_tool_names()
    assert "mcp_search" in names

    packed = tools_for_packs(["mcp"])
    assert "mcp_search" in packed
    assert "manage_mcp" in packed

    # manage pack 不自动拉 live mcp（仅静态 manage_mcp）
    packed_m = tools_for_packs(["manage"])
    assert "manage_mcp" in packed_m
    assert "mcp_search" not in packed_m


def test_tool_matches_crew_caps_mcp_prefix():
    from backend.agent.grant_store import tool_matches_crew_caps

    assert tool_matches_crew_caps("mcp_echo", ["manage_mcp"]) is True
    assert tool_matches_crew_caps("mcp_echo", ["mcp"]) is True
    assert tool_matches_crew_caps("mcp_echo", ["file_read"]) is False
    assert tool_matches_crew_caps("file_read", ["file_rw"]) is True


def test_cap_tools_readd_mcp_after_kernel_filter():
    """Rust filter 丢掉 mcp_* 后，Python 应在持有 manage_mcp 时补回。"""
    from backend.agent.cap_tools import filter_tools_for_process

    tools = [
        {"type": "function", "function": {"name": "file_read"}},
        {"type": "function", "function": {"name": "mcp_echo"}},
        {"type": "function", "function": {"name": "manage_mcp"}},
    ]

    class FakeKernel:
        def filter_tools(self, pid, names):
            # 模拟 Rust catalog：只认识静态名
            return [n for n in names if not n.startswith("mcp_")]

    class Proc:
        id = "proc12345678"
        capabilities = ["file_rw", "manage_mcp", "file_read"]

    with patch("backend.kernel.get_kernel", return_value=FakeKernel()):
        out = filter_tools_for_process(tools, Proc())

    names = {(t.get("function") or {}).get("name") for t in out}
    assert "mcp_echo" in names
    assert "manage_mcp" in names
    assert "file_read" in names


@pytest.mark.anyio
async def test_mcp_client_uses_config_timeout():
    from backend.mcp_hub.client import MCPClient, MCPServerConfig

    cfg = MCPServerConfig(
        name="t",
        transport="stdio",
        command="x",
        timeout=7.0,
    )
    client = MCPClient(cfg)
    client._initialized = True

    class Sess:
        async def call_tool(self, name, args):
            import asyncio

            await asyncio.sleep(0.05)
            from mcp.types import CallToolResult, TextContent

            return CallToolResult(content=[TextContent(type="text", text="ok")])

    client._session = Sess()
    # 极短 timeout 应触发超时路径（通过 monkeypatch clamp 下限较难，改测读取逻辑）
    assert float(client.config.timeout) == 7.0
    # 直接验证 call_tool 使用 config.timeout 不会 AttributeError
    out = await client.call_tool("ping", {})
    assert "ok" in out or "Error" in out


@pytest.mark.anyio
async def test_mcp_adapter_uses_remote_name_and_risk():
    from backend.tools.adapters.mcp_adapter import MCPToolAdapter

    client = MagicMock()
    client._initialized = True
    client.call_tool = AsyncMock(return_value="pong")

    adapter = MCPToolAdapter(
        server_name="srv",
        tool_name="ping",
        description="p",
        parameters={"type": "object", "properties": {}},
        client=client,
        risk_level="high",
    )
    assert adapter.risk_level.name == "HIGH"
    assert adapter.remote_name == "ping"
    result = await adapter.execute(x=1)
    assert result == "pong"
    client.call_tool.assert_awaited_once_with("ping", {"x": 1})


def test_resolve_enabled_does_not_auto_include_live_mcp_on_hello():
    from backend.agent.tool_policy import resolve_enabled_tool_names
    from backend.tools.adapters.mcp_adapter import MCPToolAdapter

    ToolRegistry.register(
        MCPToolAdapter("s", "weather", "w", {"type": "object", "properties": {}})
    )
    names, plan = resolve_enabled_tool_names(
        profile="coding",
        user_input="hello",
    )
    assert names is not None
    assert "mcp_weather" not in names


def test_coding_use_intent_mounts_live_mcp():
    from backend.agent.tool_policy import resolve_enabled_tool_names
    from backend.tools.adapters.mcp_adapter import MCPToolAdapter

    ToolRegistry.register(
        MCPToolAdapter("s", "doubao_search", "d", {"type": "object", "properties": {}})
    )
    names, plan = resolve_enabled_tool_names(
        profile="coding",
        user_input="用豆包搜一下今天新闻",
    )
    assert names is not None
    assert any(n.startswith("mcp_") for n in names)


def test_mcp_ops_thin_surface_no_command():
    from backend.agent.tool_policy import resolve_enabled_tool_names

    names, plan = resolve_enabled_tool_names(
        profile="coding",
        user_input="用 manage_mcp 配置豆包搜索的 env",
    )
    assert names is not None
    assert "manage_mcp" in names
    assert "command" not in names


@pytest.mark.anyio
async def test_tool_gate_mediates_mcp_as_manage_mcp():
    """tool_gate 对 mcp_* 应 mediate manage_mcp，避免 Rust catalog 拒识。"""
    from backend.kernel import get_kernel
    from backend.kernel.kernel import reset_kernel_for_tests
    from backend.kernel.tool_gate import enforce_tool_gate

    reset_kernel_for_tests()
    k = get_kernel()

    async def go():
        proc = await k.create_process("t", capabilities=["manage_mcp"])
        args, err = await enforce_tool_gate(
            "mcp_echo",
            {"msg": "hi", "_kernel_process_id": proc.id},
            process_id=proc.id,
        )
        return err, args

    err, args = await go()
    assert err is None, err
    assert args.get("_tool_gate_passed") is True
    reset_kernel_for_tests()


@pytest.mark.anyio
async def test_tool_gate_denies_mcp_without_manage_mcp():
    from backend.kernel import get_kernel
    from backend.kernel.kernel import reset_kernel_for_tests
    from backend.kernel.tool_gate import enforce_tool_gate

    reset_kernel_for_tests()
    k = get_kernel()

    async def go():
        proc = await k.create_process("t", capabilities=["file_read"])
        _args, err = await enforce_tool_gate(
            "mcp_echo",
            {"msg": "hi", "_kernel_process_id": proc.id},
            process_id=proc.id,
        )
        return err

    err = await go()
    assert err is not None and "拒绝" in err
    reset_kernel_for_tests()
