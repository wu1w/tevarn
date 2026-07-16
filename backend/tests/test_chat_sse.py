"""
Tests for SSE chat endpoint (EPIC-1A).
"""

import json
import pytest
from httpx import AsyncClient, ASGITransport
from asgi_lifespan import LifespanManager

from backend.main import app


class TestChatSSE:
    """Verify that /v1/chat/completions returns valid SSE stream."""

    @pytest.mark.asyncio
    async def test_chat_completions_returns_sse(self):
        """POST /v1/chat/completions should return SSE data."""
        async with LifespanManager(app) as manager:
            async with AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://test"
            ) as ac:
                # First auto-login to get a token
                login_resp = await ac.post("/api/auth/auto-login")
                token = login_resp.json()["access_token"]

                resp = await ac.post(
                    "/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "messages": [{"role": "user", "content": "Say hello"}],
                        "stream": True,
                    },
                )
                assert resp.status_code == 200
                assert resp.headers.get("content-type", "").startswith("text/event-stream")

                body = resp.text
                assert "data: " in body, f"Expected SSE format, got: {body[:200]}"
                assert "[DONE]" in body, "Expected [DONE] termination"

                # Parse the first data line
                for line in body.split("\n"):
                    if line.startswith("data: ") and "[DONE]" not in line:
                        chunk = json.loads(line[6:])
                        assert "choices" in chunk
                        assert chunk["choices"][0]["delta"]["role"] == "assistant"
                        break

    @pytest.mark.asyncio
    async def test_chat_completions_without_auth(self):
        """POST /v1/chat/completions without auth should work in single-user mode."""
        async with LifespanManager(app) as manager:
            async with AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://test"
            ) as ac:
                resp = await ac.post(
                    "/api/v1/chat/completions",
                    json={
                        "messages": [{"role": "user", "content": "Hi"}],
                        "stream": True,
                    },
                )
                assert resp.status_code == 200
                assert "[DONE]" in resp.text