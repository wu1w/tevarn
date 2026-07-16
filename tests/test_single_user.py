"""
Tests for single-user mode (EPIC-3A).
"""

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from asgi_lifespan import LifespanManager

from backend.main import app
from backend.core.config import settings
from backend.schemas.user import UserRead


class TestSingleUserMode:
    """Verify that single-user mode auto-creates and returns the default admin user."""

    @pytest.mark.asyncio
    async def test_auto_login_creates_default_user(self):
        """POST /auth/auto-login should create admin@takton.dev on first call."""
        async with LifespanManager(app) as manager:
            async with AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://test"
            ) as ac:
                resp = await ac.post("/api/auth/auto-login")
                assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
                data = resp.json()
                assert "access_token" in data
                assert data["user"]["email"] == "admin@takton.dev"
                assert data["user"]["username"] == "admin"
                assert data["user"]["is_superuser"] is True

    @pytest.mark.asyncio
    async def test_auto_login_idempotent(self):
        """Calling /auth/auto-login twice should return the same user."""
        async with LifespanManager(app) as manager:
            async with AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://test"
            ) as ac:
                resp1 = await ac.post("/api/auth/auto-login")
                resp2 = await ac.post("/api/auth/auto-login")
                assert resp1.status_code == 200
                assert resp2.status_code == 200
                assert resp1.json()["user"]["id"] == resp2.json()["user"]["id"]

    @pytest.mark.asyncio
    async def test_get_me_without_token_in_single_user_mode(self):
        """GET /auth/me should work without a token in single-user mode."""
        async with LifespanManager(app) as manager:
            async with AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://test"
            ) as ac:
                # First auto-login to seed the user
                await ac.post("/api/auth/auto-login")
                # Then call /auth/me without token
                resp = await ac.get("/api/auth/me")
                assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
                data = resp.json()
                assert data["email"] == "admin@takton.dev"

    @pytest.mark.asyncio
    async def test_single_user_mode_disabled_returns_403(self):
        """When single_user_mode=False, /auth/auto-login should return 403."""
        original = settings.single_user_mode
        settings.single_user_mode = False
        try:
            async with LifespanManager(app) as manager:
                async with AsyncClient(
                    transport=ASGITransport(app=manager.app), base_url="http://test"
                ) as ac:
                    resp = await ac.post("/api/auth/auto-login")
                    assert resp.status_code == 403
        finally:
            settings.single_user_mode = original