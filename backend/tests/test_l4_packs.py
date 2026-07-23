"""L4 sidecar packs: devices / desktop / evolution DoD."""
from __future__ import annotations

import asyncio

import pytest

from backend.agent.pack_catalog import (
    L4_PRIMARY_PACKS,
    assert_pack_registered,
    coding_plus_packs,
    pack_tool_names,
)
from backend.agent.tool_policy import resolve_enabled_tool_names
from backend.tools.loader import load_all_tools
from backend.tools.registry import ToolRegistry


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.mark.asyncio
async def test_l4_packs_fully_registered():
    await load_all_tools()
    reg = set(ToolRegistry._tools.keys())
    for pack in L4_PRIMARY_PACKS:
        miss = assert_pack_registered(reg, pack)
        assert not miss, f"{pack} missing tools: {miss}"


@pytest.mark.asyncio
async def test_coding_excludes_sidecar_heavy_tools():
    await load_all_tools()
    names, plan = resolve_enabled_tool_names(profile="coding", user_input="hi")
    assert names is not None
    assert plan.profile == "coding"
    assert "desktop_click" not in names
    assert "manage_evolution" not in names
    assert "manage_cron" not in names
    # devices moved out of default whitelist
    assert "list_devices_tool" not in names
    assert "remote_exec" not in names
    assert "use_tool_pack" in names
    assert "file_read" in names


@pytest.mark.asyncio
async def test_use_tool_pack_expand_devices_desktop_evolution():
    await load_all_tools()
    d = coding_plus_packs("devices")
    assert d is not None
    assert "list_devices_tool" in d
    assert "remote_exec" in d

    desk = coding_plus_packs("desktop")
    assert desk is not None
    assert "desktop_click" in desk
    assert "desktop_screenshot" in desk

    evo = coding_plus_packs("evolution")
    assert evo is not None
    assert "manage_evolution" in evo
    assert "query_evolution" in evo


@pytest.mark.asyncio
async def test_desktop_descriptions_thickened():
    await load_all_tools()
    for name in ("desktop_click", "desktop_screenshot", "desktop_type"):
        tool = ToolRegistry.get(name)
        assert tool is not None
        assert len(tool.description or "") >= 40, name


@pytest.mark.asyncio
async def test_use_tool_pack_list_mentions_l4():
    await load_all_tools()
    tool = ToolRegistry.get("use_tool_pack")
    assert tool is not None
    out = await tool.execute(action="list")
    text = str(out)
    for pack in L4_PRIMARY_PACKS:
        assert pack in text


def test_pack_tool_names_nonempty():
    for pack in L4_PRIMARY_PACKS:
        assert len(pack_tool_names(pack)) >= 3
