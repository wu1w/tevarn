"""SubAgent / cluster mode wiring tests."""

from __future__ import annotations

import ast
import inspect
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.api.routes import sub_agents as sub_agents_mod
from backend.agent.loop import NexusAgentLoop


ROOT = Path(__file__).resolve().parents[1]


def test_backend_sources_parse():
    for rel in (
        "backend/agent/loop.py",
        "backend/api/routes/sub_agents.py",
        "backend/api/websocket.py",
    ):
        ast.parse((ROOT / rel).read_text(encoding="utf-8"))


def test_run_accepts_sub_agent_ids():
    sig = inspect.signature(NexusAgentLoop.run)
    assert "sub_agent_ids" in sig.parameters


def test_subagents_routes_registered():
    paths = {getattr(r, "path", "") for r in sub_agents_mod.router.routes}
    assert any("model-inventory" in p for p in paths)
    assert "/subagents" in paths or any(p.endswith("") for p in paths)
    # CRUD path present
    assert any("{agent_id}" in (p or "") for p in paths)


@pytest.mark.asyncio
async def test_build_inventory_returns_list():
    inv = await sub_agents_mod._build_inventory_from_catalog()
    assert isinstance(inv, list)
    for item in inv:
        assert item.ref
        assert item.model_name
        assert item.provider_id


@pytest.mark.asyncio
async def test_build_inventory_from_catalog_shape():
    fake_catalog = {
        "active_provider_id": "openai",
        "active_model": "gpt-4o",
        "default_provider_id": "openai",
        "default_model": "gpt-4o",
        "fallback_provider_id": "deepseek",
        "fallback_model": "deepseek-v3",
        "providers": [
            {
                "id": "openai",
                "name": "OpenAI",
                "icon": "🤖",
                "enabled": True,
                "llm_provider": "openai",
                "llm_base_url": "https://api.openai.com/v1",
                "llm_api_key": "sk-test",
                "cached_models": ["gpt-4o", "gpt-4o-mini"],
                "disabled_models": ["gpt-4o-mini"],
            },
            {
                "id": "deepseek",
                "name": "DeepSeek",
                "icon": "🧠",
                "enabled": True,
                "llm_provider": "openai-compatible",
                "llm_base_url": "https://api.deepseek.com",
                "has_api_key": True,
                "cached_models": ["deepseek-v3"],
            },
        ],
    }

    with patch("backend.core.model_catalog.load_catalog", new=AsyncMock(return_value=fake_catalog)):
        with patch("backend.repositories.setting_repo.AsyncSettingRepository"):
            inv = await sub_agents_mod._build_inventory_from_catalog()

    refs = {i.ref for i in inv}
    assert "openai/gpt-4o" in refs
    assert "openai/gpt-4o-mini" not in refs  # disabled
    assert "deepseek/deepseek-v3" in refs
    active = next(i for i in inv if i.ref == "openai/gpt-4o")
    assert active.status == "active"


def test_frontend_cluster_wiring_markers():
    checks = {
        "frontend/components/chat/MessageInput.tsx": ["ClusterModePanel", "cluster", "subAgentIds"],
        "frontend/lib/ws.ts": ["sub_agent_ids"],
        "frontend/app/page.tsx": ["subAgentIds", "cluster"],
        "frontend/components/layout/Sidebar.tsx": ["/profiles"],
        "frontend/app/profiles/page.tsx": ["SubAgentPanel"],
        "frontend/components/subagent/SubAgentPanel.tsx": ["任务名称", "ClusterModePanel"],
    }
    for rel, needles in checks.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        for n in needles:
            assert n in text, f"{rel} missing {n!r}"


@pytest.mark.asyncio
async def test_websocket_run_agent_safe_forwards_sub_agent_ids():
    from backend.api import websocket as wsmod

    agent = MagicMock()
    agent.run = AsyncMock(return_value="ok")
    sid = uuid.uuid4()
    await wsmod._run_agent_safe(
        agent,
        sid,
        "hello cluster",
        attachments=[],
        mode="cluster",
        sub_agent_ids=["a", "b"],
    )
    agent.run.assert_awaited_once()
    kwargs = agent.run.await_args.kwargs
    assert kwargs.get("mode") == "cluster"
    assert kwargs.get("sub_agent_ids") == ["a", "b"]
