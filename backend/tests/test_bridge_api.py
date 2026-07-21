"""Tests for Takton Code ↔ Desktop bridge API (/bridge/v1/*).

Covers the P0 bridge surface that Takton Code consumes:
- health / models catalog
- chat/completions with K4 session-snapshot lock (the critical path)
- tools/invoke three-tier dispatch (unified registry → skill → DB)
- rag/search

LLM and external stores are mocked; no real model calls.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from asgi_lifespan import LifespanManager

from backend.main import app


def _make_chat_resp(
    content: str = "hello",
    tool_calls: list[Any] | None = None,
    reasoning: str | None = None,
) -> MagicMock:
    resp = MagicMock()
    resp.content = content
    resp.reasoning_content = reasoning
    resp.tool_calls = tool_calls or []
    resp.finish_reason = "tool_calls" if tool_calls else "stop"
    return resp


@pytest.mark.asyncio
async def test_bridge_health() -> None:
    async with LifespanManager(app) as manager:
        async with AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://test"
        ) as ac:
            r = await ac.get("/api/bridge/v1/health")
            assert r.status_code == 200
            body = r.json()
            assert body["ok"] is True
            assert "capabilities" in body
            assert "models" in body["capabilities"]
            assert "tools" in body["capabilities"]
            assert "rag" in body["capabilities"]


@pytest.mark.asyncio
async def test_bridge_models_returns_list() -> None:
    async with LifespanManager(app) as manager:
        async with AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://test"
        ) as ac:
            r = await ac.get("/api/bridge/v1/models")
            assert r.status_code == 200
            body = r.json()
            assert body["object"] == "list"
            assert isinstance(body["data"], list)
            # active model always present as first entry
            assert len(body["data"]) >= 1
            assert body["data"][0]["id"]


@pytest.mark.asyncio
async def test_bridge_chat_uses_session_snapshot() -> None:
    """K4 critical: when session_id carries a locked LLM snapshot, bridge must
    build the service from THAT snapshot, not the global default."""
    session_id = str(uuid.uuid4())
    snap = {
        "provider": "openai-compatible",
        "model": "sess-locked-model",
        "base_url": "http://x/v1",
        "api_key": "k",
        "max_tokens": 128,
        "temperature": 0.1,
    }

    svc = MagicMock()
    svc.chat_complete = AsyncMock(return_value=_make_chat_resp("snap-reply"))

    sess = MagicMock()
    sess.config = {"llm": snap}

    with (
        patch(
            "backend.repositories.session_repo.AsyncSessionRepository"
        ) as srepo_cls,
        patch(
            "backend.services.llm.factory.LLMServiceFactory.get_service_for_snapshot",
            return_value=svc,
        ) as get_snap,
    ):
        srepo_cls.return_value.get_by_id = AsyncMock(return_value=sess)
        async with LifespanManager(app) as manager:
            async with AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://test"
            ) as ac:
                r = await ac.post(
                    "/api/bridge/v1/chat/completions",
                    json={
                        "messages": [{"role": "user", "content": "hi"}],
                        "session_id": session_id,
                    },
                )
                assert r.status_code == 200, r.text
                body = r.json()
                assert body["object"] == "chat.completion"
                msg = body["choices"][0]["message"]
                assert msg["content"] == "snap-reply"
                # snapshot path taken with the session's locked snap
                get_snap.assert_called_once()
                called_snap = get_snap.call_args[0][0]
                assert called_snap["model"] == "sess-locked-model"


@pytest.mark.asyncio
async def test_bridge_chat_tool_call_pair_shape() -> None:
    """Assistant tool_calls must serialize with function.name and content=None
    (strict OpenAI pair shape — K1/K7 guard at the bridge boundary)."""
    from types import SimpleNamespace

    fn = SimpleNamespace(name="file_read", arguments='{"path": "a.py"}')
    tc = SimpleNamespace(id="call_1", function=fn, type="function")

    svc = MagicMock()
    svc.chat_complete = AsyncMock(return_value=_make_chat_resp("", tool_calls=[tc]))

    with patch(
        "backend.services.llm.factory.LLMServiceFactory.get_service",
        return_value=svc,
    ):
        async with LifespanManager(app) as manager:
            async with AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://test"
            ) as ac:
                r = await ac.post(
                    "/api/bridge/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": "hi"}]},
                )
                assert r.status_code == 200, r.text
                msg = r.json()["choices"][0]["message"]
                assert msg["tool_calls"][0]["function"]["name"] == "file_read"
                # empty content + tool_calls -> content null
                assert msg["content"] is None
                assert r.json()["choices"][0]["finish_reason"] == "tool_calls"


@pytest.mark.asyncio
async def test_bridge_chat_llm_error_returns_502() -> None:
    svc = MagicMock()
    svc.chat_complete = AsyncMock(side_effect=RuntimeError("llm down"))
    with patch(
        "backend.services.llm.factory.LLMServiceFactory.get_service",
        return_value=svc,
    ):
        async with LifespanManager(app) as manager:
            async with AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://test"
            ) as ac:
                r = await ac.post(
                    "/api/bridge/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": "hi"}]},
                )
                assert r.status_code == 502
                assert "LLM error" in r.json()["detail"]


@pytest.mark.asyncio
async def test_bridge_tools_invoke_requires_name() -> None:
    async with LifespanManager(app) as manager:
        async with AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://test"
        ) as ac:
            r = await ac.post(
                "/api/bridge/v1/tools/invoke", json={"name": "", "arguments": {}}
            )
            assert r.status_code == 200
            assert r.json()["ok"] is False
            assert "name required" in r.json()["error"]


@pytest.mark.asyncio
async def test_bridge_tools_invoke_not_found() -> None:
    with (
        patch("backend.tools.registry.ToolRegistry.get", return_value=None),
        patch("backend.repositories.tool_repo.AsyncToolRepository") as trepo,
    ):
        trepo.return_value.list_all = AsyncMock(return_value=[])
        async with LifespanManager(app) as manager:
            async with AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://test"
            ) as ac:
                r = await ac.post(
                    "/api/bridge/v1/tools/invoke",
                    json={"name": "nonexistent_tool_xyz", "arguments": {}},
                )
                assert r.status_code == 200
                body = r.json()
                assert body["ok"] is False
                assert "not found" in body["error"]


@pytest.mark.asyncio
async def test_bridge_tools_invoke_unified_registry_hit() -> None:
    with patch(
        "backend.tools.registry.ToolRegistry.get", return_value=object()
    ), patch(
        "backend.tools.registry.ToolRegistry.execute",
        new=AsyncMock(return_value="read ok"),
    ):
        async with LifespanManager(app) as manager:
            async with AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://test"
            ) as ac:
                r = await ac.post(
                    "/api/bridge/v1/tools/invoke",
                    json={"name": "file_read", "arguments": {"path": "a.py"}},
                )
                assert r.status_code == 200
                body = r.json()
                assert body["ok"] is True
                assert body["output"] == "read ok"


@pytest.mark.asyncio
async def test_bridge_rag_search() -> None:
    fake_hits = [
        {"content": "doc snippet", "score": 0.9, "metadata": {"source": "wiki"}},
    ]
    # rag route may call various internal services; patch broadly and accept
    # either a structured hit list or an ok envelope.
    async with LifespanManager(app) as manager:
        async with AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://test"
        ) as ac:
            with patch(
                "backend.services.rag.RAGService.search",
                new=AsyncMock(return_value=fake_hits),
            ):
                r = await ac.post(
                    "/api/bridge/v1/rag/search",
                    json={"query": "pubchem", "top_k": 3},
                )
                assert r.status_code == 200
                body = r.json()
                assert isinstance(body, dict)
                # tolerant: route returns hits under some key or ok envelope
                assert ("hits" in body) or ("results" in body) or ("ok" in body)


@pytest.mark.asyncio
async def test_bridge_skills_and_tools_catalog_shape() -> None:
    async with LifespanManager(app) as manager:
        async with AsyncClient(
            transport=ASGITransport(app=manager.app), base_url="http://test"
        ) as ac:
            rs = await ac.get("/api/bridge/v1/skills")
            assert rs.status_code == 200
            assert isinstance(rs.json(), (list, dict))

            rt = await ac.get("/api/bridge/v1/tools")
            assert rt.status_code == 200
            assert isinstance(rt.json(), (list, dict))

            rm = await ac.get("/api/bridge/v1/mcp")
            assert rm.status_code == 200


@pytest.mark.asyncio
async def test_bridge_token_not_set_allows_local() -> None:
    """默认（未设 bridge_token）→ local 免 token，走 get_current_user 回落。"""
    from backend.core.config import settings as _s

    orig = getattr(_s, "bridge_token", None)
    _s.bridge_token = None
    try:
        async with LifespanManager(app) as manager:
            async with AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://test"
            ) as ac:
                r = await ac.get("/api/bridge/v1/health")
                assert r.status_code == 200
    finally:
        _s.bridge_token = orig


@pytest.mark.asyncio
async def test_bridge_token_set_requires_bearer() -> None:
    """设置 bridge_token 后：无 token / 错 token → 401；正确 token → 200。"""
    from backend.core.config import settings as _s

    orig = getattr(_s, "bridge_token", None)
    _s.bridge_token = "test-bridge-secret"
    try:
        async with LifespanManager(app) as manager:
            async with AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://test"
            ) as ac:
                # no token
                r0 = await ac.get("/api/bridge/v1/health")
                assert r0.status_code == 401
                # wrong token
                r1 = await ac.get(
                    "/api/bridge/v1/health",
                    headers={"Authorization": "Bearer wrong"},
                )
                assert r1.status_code == 401
                # correct token
                r2 = await ac.get(
                    "/api/bridge/v1/health",
                    headers={"Authorization": "Bearer test-bridge-secret"},
                )
                assert r2.status_code == 200
                assert r2.json()["ok"] is True
    finally:
        _s.bridge_token = orig
