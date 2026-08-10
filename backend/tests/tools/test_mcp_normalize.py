"""MCP normalize + package rewrite tests."""

from backend.mcp_hub.normalize import (
    is_redacted_secret,
    merge_env,
    normalize_server_fields,
    normalize_stdio_command_args,
)


def test_rewrite_bad_tavily_package():
    cmd, args = normalize_stdio_command_args(
        "tavily", "npx", ["-y", "@tavily/mcp-server"]
    )
    assert "tavily-mcp" in " ".join(args)
    assert "@tavily/mcp-server" not in " ".join(args)


def test_merge_env_keeps_secret():
    old = {"TAVILY_API_KEY": "tvly-real-key-123"}
    new = {"TAVILY_API_KEY": "***", "OTHER": "x"}
    m = merge_env(old, new)
    assert m["TAVILY_API_KEY"] == "tvly-real-key-123"
    assert m["OTHER"] == "x"


def test_redacted_detection():
    assert is_redacted_secret("")
    assert is_redacted_secret("***")
    assert is_redacted_secret("[REDACTED]")
    assert not is_redacted_secret("tvly-dev-abcdef")


def test_normalize_server_fields_tavily_default():
    n = normalize_server_fields(name="tavily", command="npx", args=[], env={})
    assert "tavily-mcp" in " ".join(n["args"])


def test_tools_include_exclude():
    from backend.mcp_hub.normalize import tool_name_allowed

    # 全量
    assert tool_name_allowed("tavily_search", None, None) is True
    # include 白名单
    assert tool_name_allowed("tavily_search", ["tavily_search"], None) is True
    assert tool_name_allowed("tavily_crawl", ["tavily_search"], None) is False
    # fnmatch
    assert tool_name_allowed("tavily_search", ["tavily_*"], None) is True
    assert tool_name_allowed("other", ["tavily_*"], None) is False
    # exclude（仅 include 空时）
    assert tool_name_allowed("tavily_crawl", None, ["tavily_crawl"]) is False
    assert tool_name_allowed("tavily_search", None, ["tavily_crawl"]) is True
    # include 优先于 exclude
    assert tool_name_allowed("tavily_search", ["tavily_*"], ["tavily_search"]) is True


def test_register_respects_include(clean_registry=None):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from backend.tools.adapters.mcp_adapter import register_mcp_server_tools
    from backend.tools.base import ToolSource
    from backend.tools.registry import ToolRegistry

    ToolRegistry.clear()

    class FakeTool:
        def __init__(self, name, description=""):
            self.name = name
            self.description = description
            self.inputSchema = {"type": "object", "properties": {}}

    class FakeList:
        def __init__(self, tools):
            self.tools = tools

    client = MagicMock()
    client.list_tools = AsyncMock(
        return_value=FakeList(
            [FakeTool("tavily_search"), FakeTool("tavily_crawl"), FakeTool("tavily_map")]
        )
    )
    client._initialized = True

    async def go():
        n = await register_mcp_server_tools(
            "tavily",
            client,
            tools_include=["tavily_search", "tavily_map"],
        )
        return n

    n = asyncio.get_event_loop().run_until_complete(go()) if False else None
    # anyio-friendly
    n = __import__("asyncio").run(go())
    assert n == 2
    names = {t.name for t in ToolRegistry.get_all(source=ToolSource.MCP)}
    assert names == {"mcp_tavily_search", "mcp_tavily_map"}
    ToolRegistry.clear()
