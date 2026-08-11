"""streamable-http transport + connect timeout + install-fail conclude."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.mcp_hub.client import (
    MCPClient,
    MCPClientManager,
    MCPServerConfig,
    _connect_timeout_seconds,
    _normalize_transport,
)
from backend.mcp_hub.normalize import (
    SUPPORTED_TRANSPORTS,
    is_url_transport,
    normalize_transport,
)


def test_normalize_transport_aliases():
    assert normalize_transport("streamable-http") == "streamable-http"
    assert normalize_transport("streamable_http") == "streamable-http"
    assert normalize_transport("HTTP") == "streamable-http"
    assert normalize_transport("sse") == "sse"
    assert normalize_transport("stdio") == "stdio"
    assert "streamable-http" in SUPPORTED_TRANSPORTS
    assert is_url_transport("http")
    assert is_url_transport("streamable-http")
    assert not is_url_transport("stdio")


def test_connect_timeout_mcp_remote_floor():
    cfg = MCPServerConfig(
        name="deepwiki",
        transport="stdio",
        command="npx",
        args=["-y", "mcp-remote@latest", "https://mcp.deepwiki.com/mcp"],
        timeout=30.0,
    )
    assert _connect_timeout_seconds(cfg) >= 120.0

    cfg2 = MCPServerConfig(
        name="x",
        transport="streamable-http",
        url="https://mcp.deepwiki.com/mcp",
        timeout=45.0,
    )
    assert _connect_timeout_seconds(cfg2) == 45.0


def test_client_normalizes_transport_on_init():
    c = MCPClient(
        MCPServerConfig(
            name="dw",
            transport="http",
            url="https://mcp.deepwiki.com/mcp",
        )
    )
    assert c.config.transport == "streamable-http"


@pytest.mark.asyncio
async def test_connect_streamable_http_path():
    """_connect_streamable_http 使用 SDK streamable_http_client + ClientSession."""
    cfg = MCPServerConfig(
        name="dw",
        transport="streamable-http",
        url="https://mcp.deepwiki.com/mcp",
        timeout=15.0,
    )
    client = MCPClient(cfg)

    fake_read, fake_write = object(), object()
    mock_session = AsyncMock()
    mock_session.initialize = AsyncMock()
    mock_tools = MagicMock()
    mock_tools.tools = []
    mock_session.list_tools = AsyncMock(return_value=mock_tools)

    class _Ctx:
        def __init__(self, value):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, *a):
            return False

    # 强制走 modern 分支的 default client 路径（跳过 create_mcp_http_client 分支）
    with (
        patch(
            "mcp.client.streamable_http.streamable_http_client",
            return_value=_Ctx((fake_read, fake_write)),
        ),
        patch(
            "mcp.client.streamable_http.create_mcp_http_client",
            side_effect=RuntimeError("force default path"),
        ),
        patch(
            "backend.mcp_hub.client.ClientSession",
            return_value=_Ctx(mock_session),
        ),
    ):
        await client.connect()

    assert client._initialized is True
    mock_session.initialize.assert_awaited()


@pytest.mark.asyncio
async def test_reconnect_records_last_error():
    mgr = MCPClientManager()
    cfg = MCPServerConfig(
        name="bad",
        transport="streamable-http",
        url="https://example.invalid/mcp",
        timeout=10.0,
    )

    with patch.object(
        MCPClient,
        "connect",
        new=AsyncMock(side_effect=TimeoutError("connect timed out after 10s")),
    ):
        out = await mgr.reconnect(cfg)

    assert out is None
    err = mgr.last_error("bad")
    assert err is not None
    assert "TimeoutError" in err or "timed out" in err


@pytest.mark.asyncio
async def test_manage_mcp_connect_fail_conclude_message():
    from backend.tools.builtins.manage_integration_tools import ManageMcp

    msg = ManageMcp._connect_fail_conclude_message(
        name="deepwiki",
        action="add",
        err="TimeoutError: connect timed out after 60s",
        transport="streamable-http",
    )
    assert "【自动收束】" in msg
    assert "deepwiki" in msg
    assert "停止" in msg


def test_deepwiki_presets_exist():
    from backend.services.mcp_presets import find_preset

    p = find_preset("deepwiki")
    assert p is not None
    assert p.transport == "streamable-http"
    assert "deepwiki.com" in (p.url or "")
    assert p.timeout >= 60.0

    p2 = find_preset("mcp-remote")
    assert p2 is not None
    assert p2.timeout >= 120.0
    assert p2.runners


def test_curated_deepwiki_streamable():
    from backend.services.mcp_store import CURATED

    dw = next((x for x in CURATED if x.id == "deepwiki"), None)
    assert dw is not None
    assert dw.transport == "streamable-http"
    assert dw.url.startswith("https://")
