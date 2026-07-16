"""
MCP 集成测试（Week 2 Day 10）

验证 MCP 工具适配器核心逻辑：
- 工具注册到 ToolRegistry
- schema 生成
- 执行时 mcp_ 前缀剥离
- 来源和风险等级

使用 mock MCPClient 避免真实子进程超时问题。
真实 stdio 连接测试在手动验证中已通过。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.mcp_hub.client import MCPClient, MCPClientManager
from backend.tools.adapters.mcp_adapter import MCPToolAdapter, register_mcp_server_tools
from backend.tools.base import ToolSource, ToolRiskLevel
from backend.tools.registry import ToolRegistry


class FakeTool:
    def __init__(self, name: str, description: str, input_schema: dict):
        self.name = name
        self.description = description
        self.inputSchema = input_schema


class FakeListToolsResult:
    def __init__(self, tools):
        self.tools = tools


@pytest.fixture(autouse=True)
def clean_registry():
    ToolRegistry.clear()
    yield
    ToolRegistry.clear()


@pytest.fixture
def fake_mcp_client():
    """Create a mock MCPClient with 3 tools: echo, add, upper"""
    client = MagicMock(spec=MCPClient)
    client.name = "integration-test-server"
    client._initialized = True

    client.list_tools = AsyncMock(
        return_value=FakeListToolsResult(
            [
                FakeTool(
                    "echo",
                    "Echo the input message",
                    {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                    },
                ),
                FakeTool(
                    "add",
                    "Add two numbers",
                    {
                        "type": "object",
                        "properties": {
                            "a": {"type": "number"},
                            "b": {"type": "number"},
                        },
                        "required": ["a", "b"],
                    },
                ),
                FakeTool(
                    "upper",
                    "Convert text to uppercase",
                    {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    },
                ),
            ]
        )
    )

    # Simulate real server behavior: strip mcp_ prefix for call_tool
    async def _call_tool(name: str, args: dict) -> str:
        if name == "echo":
            return f"echo: {args.get('message', '')}"
        elif name == "add":
            return str(args.get("a", 0) + args.get("b", 0))
        elif name == "upper":
            return args.get("text", "").upper()
        return f"Unknown tool: {name}"

    client.call_tool = AsyncMock(side_effect=_call_tool)
    return client


# ─── Test: Registration ───


@pytest.mark.anyio
async def test_register_mcp_server_tools_count(fake_mcp_client):
    """3 tools registered from MCP server"""
    count = await register_mcp_server_tools("integration-test-server", fake_mcp_client)
    assert count == 3


@pytest.mark.anyio
async def test_register_mcp_tools_have_prefix(fake_mcp_client):
    """All MCP tools get mcp_ prefix in ToolRegistry"""
    await register_mcp_server_tools("integration-test-server", fake_mcp_client)
    tools = ToolRegistry.get_by_source("mcp")
    names = {t.name for t in tools}
    assert names == {"mcp_echo", "mcp_add", "mcp_upper"}


# ─── Test: Execution ───


@pytest.mark.anyio
async def test_execute_echo(fake_mcp_client):
    """mcp_echo executes and returns correct result"""
    await register_mcp_server_tools("integration-test-server", fake_mcp_client)
    result = await ToolRegistry.execute("mcp_echo", {"message": "hello"})
    assert result == "echo: hello"


@pytest.mark.anyio
async def test_execute_add(fake_mcp_client):
    """mcp_add executes and returns correct result"""
    await register_mcp_server_tools("integration-test-server", fake_mcp_client)
    result = await ToolRegistry.execute("mcp_add", {"a": 7, "b": 3})
    assert result == "10"


@pytest.mark.anyio
async def test_execute_upper(fake_mcp_client):
    """mcp_upper executes and returns correct result"""
    await register_mcp_server_tools("integration-test-server", fake_mcp_client)
    result = await ToolRegistry.execute("mcp_upper", {"text": "takton"})
    assert result == "TAKTON"


# ─── Test: Prefix stripping ───


@pytest.mark.anyio
async def test_prefix_strip_on_call(fake_mcp_client):
    """MCPToolAdapter strips mcp_ prefix when calling remote server"""
    await register_mcp_server_tools("integration-test-server", fake_mcp_client)
    await ToolRegistry.execute("mcp_echo", {"message": "test"})

    # Verify call_tool was called with the original name (without mcp_ prefix)
    fake_mcp_client.call_tool.assert_awaited_with("echo", {"message": "test"})


# ─── Test: Schema generation ───


@pytest.mark.anyio
async def test_schema_generation(fake_mcp_client):
    """MCP tools generate correct JSON schemas for LLM"""
    await register_mcp_server_tools("integration-test-server", fake_mcp_client)
    schemas = ToolRegistry.get_tools_schema()
    schema_map = {s["function"]["name"]: s for s in schemas}

    assert "mcp_echo" in schema_map
    assert "mcp_add" in schema_map
    assert "mcp_upper" in schema_map

    # Verify echo schema structure
    echo_schema = schema_map["mcp_echo"]
    assert echo_schema["type"] == "function"
    assert echo_schema["function"]["description"] == "Echo the input message"
    assert "message" in echo_schema["function"]["parameters"]["properties"]


# ─── Test: Source and risk ───


@pytest.mark.anyio
async def test_source_and_risk_level(fake_mcp_client):
    """MCP tools have correct source and default risk level"""
    await register_mcp_server_tools("integration-test-server", fake_mcp_client)
    for tool in ToolRegistry.get_by_source("mcp"):
        assert tool.source == ToolSource.MCP
        assert tool.risk_level == ToolRiskLevel.MEDIUM


# ─── Test: Error handling ───


@pytest.mark.anyio
async def test_tool_not_found_error():
    """Calling non-existent tool returns error"""
    result = await ToolRegistry.execute("mcp_nonexistent", {})
    assert "[Error]" in result


@pytest.mark.anyio
async def test_disconnected_server_error():
    """MCPToolAdapter returns error when server not connected"""
    adapter = MCPToolAdapter(
        server_name="offline-server",
        tool_name="test",
        description="test tool",
        parameters={"type": "object", "properties": {}},
        client=None,
        client_manager=None,
    )
    result = await adapter.execute()
    assert "[Error]" in result
    assert "not connected" in result
