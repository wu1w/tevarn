"""回归：config_intent streamable-http + skill 热挂 + SSRF 护栏。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.services.config_intent import _infer_remote_mcp_transport, _detect_mcp_add
from backend.skills.runtime_sync import (
    register_dynamic_skill,
    unregister_dynamic_skill,
    sync_dynamic_skill_row,
)


def test_infer_remote_transport():
    assert _infer_remote_mcp_transport("https://mcp.deepwiki.com/mcp") == "streamable-http"
    assert _infer_remote_mcp_transport("https://example.com/sse") == "sse"
    assert _infer_remote_mcp_transport("https://host/v1/sse/") == "sse"
    assert _infer_remote_mcp_transport("https://api.example.com/v1") == "streamable-http"


def test_detect_mcp_add_prefers_streamable_http():
    m = _detect_mcp_add("添加 MCP deepwiki url=https://mcp.deepwiki.com/mcp")
    assert m is not None
    assert m.kind == "mcp_add_custom"
    assert m.payload.get("transport") == "streamable-http"
    assert "deepwiki" in (m.payload.get("url") or "")


def test_detect_mcp_add_sse_path():
    m = _detect_mcp_add("添加 MCP name=legacy url=https://host.example/sse")
    assert m is not None
    assert m.payload.get("transport") == "sse"


def test_register_dynamic_skill_hot():
    from backend.tools.registry import ToolRegistry

    name = "_test_dyn_skill_hot"
    unregister_dynamic_skill(name)

    row = MagicMock()
    row.name = name
    row.description = "t"
    row.schema = {"type": "object", "properties": {}}
    row.handler = "http"
    row.handler_config = {"url": "https://example.com/x"}
    row.enabled = True
    row.is_builtin = False

    assert register_dynamic_skill(row) is True
    assert ToolRegistry.get(name) is not None

    row.enabled = False
    sync = sync_dynamic_skill_row(row)
    assert sync["registered"] is False
    assert ToolRegistry.get(name) is None

    unregister_dynamic_skill(name)


@pytest.mark.asyncio
async def test_skill_md_fetch_blocks_private_url():
    from backend.services.skill_store.skill_md_storage import SkillMdDownloader

    # 内网 / 元数据地址应被 validate_public_url 拦截
    out = await SkillMdDownloader._fetch_text("http://127.0.0.1:8080/SKILL.md")
    assert out is None
    out2 = await SkillMdDownloader._fetch_text("http://169.254.169.254/latest/meta-data/")
    assert out2 is None
