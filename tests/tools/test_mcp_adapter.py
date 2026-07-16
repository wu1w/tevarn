"""
MCP 工具适配器测试

- 使用内存 MCPClient 模拟 server 返回工具列表
- 验证 MCPToolAdapter 注册和调用
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.mcp_hub.client import MCPClient, MCPClientManager
from backend.tools.adapters.mcp_adapter import MCPToolAdapter, register_mcp_server_tools
from backend.tools.base import ToolSource
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
def fake_manager():
    manager = MCPClientManager()
    client = MagicMock(spec=MCPClient)
    client.name = "fake-server"
    client._initialized = True

    # list_tools 返回两个工具
    client.list_tools = AsyncMock(
        return_value=FakeListToolsResult(
            [
                FakeTool(
                    "echo",
                    "Echo the input",
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
                        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                        "required": ["a", "b"],
                    },
                ),
            ]
        )
    )
    client.call_tool = AsyncMock(return_value="mock result")
    manager._clients = {"fake-server": client}
    return manager


@pytest.mark.anyio
async def test_register_mcp_server_tools(fake_manager):
    client = fake_manager.get_client("fake-server")
    count = await register_mcp_server_tools("fake-server", client)
    assert count == 2

    tools = ToolRegistry.get_all(source=ToolSource.MCP)
    assert len(tools) == 2
    names = {t.name for t in tools}
    assert names == {"mcp_echo", "mcp_add"}


@pytest.mark.anyio
async def test_mcp_tool_schema(fake_manager):
    client = fake_manager.get_client("fake-server")
    await register_mcp_server_tools("fake-server", client)

    echo = ToolRegistry.get("mcp_echo")
    assert echo is not None
    schema = echo.to_json_schema()
    assert schema["function"]["name"] == "mcp_echo"
    assert "message" in schema["function"]["parameters"]["properties"]


@pytest.mark.anyio
async def test_mcp_tool_execute(fake_manager):
    client = fake_manager.get_client("fake-server")
    await register_mcp_server_tools("fake-server", client)

    result = await ToolRegistry.execute("mcp_echo", {"message": "hello"})
    assert result == "mock result"
    # The adapter strips the mcp_ prefix before calling the remote server
    client.call_tool.assert_awaited_once_with("echo", {"message": "hello"})


@pytest.mark.anyio
async def test_mcp_tool_adapter_source_and_risk():
    adapter = MCPToolAdapter(
        server_name="test",
        tool_name="demo",
        description="demo tool",
        parameters={"type": "object", "properties": {}},
    )
    assert adapter.source == ToolSource.MCP
    assert adapter.risk_level.name == "MEDIUM"
