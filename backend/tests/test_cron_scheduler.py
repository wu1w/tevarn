"""
Tests for Cron scheduler (EPIC-2B).
"""

import pytest
from httpx import AsyncClient, ASGITransport
from asgi_lifespan import LifespanManager

from backend.main import app
from backend.services.cron_scheduler import CronScheduler


class TestCronScheduler:
    """Verify that the cron scheduler registers, triggers, and stops."""

    @pytest.mark.asyncio
    async def test_scheduler_start_stop(self):
        """CronScheduler should start and stop without errors."""
        scheduler = CronScheduler()
        assert scheduler._running is False

        await scheduler.start()
        assert scheduler._running is True

        await scheduler.stop()
        assert scheduler._running is False

    @pytest.mark.asyncio
    async def test_scheduler_idempotent_start(self):
        """Starting an already-running scheduler should log a warning, not crash."""
        scheduler = CronScheduler()
        await scheduler.start()
        await scheduler.start()  # second start should be a no-op
        assert scheduler._running is True
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_calculate_delay_every_minute(self):
        """_calculate_delay should return 60 for 'every 1m'."""
        from datetime import datetime, timezone
        scheduler = CronScheduler()
        now = datetime.now(timezone.utc)
        delay = scheduler._calculate_delay("every 1m", now)
        assert delay is not None
        assert 55 <= delay <= 65  # allow small timing variance

    @pytest.mark.asyncio
    async def test_calculate_delay_every_hour(self):
        """_calculate_delay should return 3600 for 'every 1h'."""
        from datetime import datetime, timezone
        scheduler = CronScheduler()
        now = datetime.now(timezone.utc)
        delay = scheduler._calculate_delay("every 1h", now)
        assert delay is not None
        assert 3595 <= delay <= 3605

    @pytest.mark.asyncio
    async def test_cron_api_create_and_list(self):
        """CRUD: create a cron job via API, then list it."""
        async with LifespanManager(app) as manager:
            async with AsyncClient(
                transport=ASGITransport(app=manager.app), base_url="http://test"
            ) as ac:
                # Auto-login to get token
                login_resp = await ac.post("/api/auth/auto-login")
                token = login_resp.json()["access_token"]
                headers = {"Authorization": f"Bearer {token}"}

                # Create
                create_resp = await ac.post(
                    "/api/cron",
                    headers=headers,
                    json={
                        "name": "test-job",
                        "schedule": "every 5m",
                        "enabled": True,
                    },
                )
                assert create_resp.status_code == 200, f"Create failed: {create_resp.text}"
                job_id = create_resp.json()["id"]

                # List
                list_resp = await ac.get("/api/cron", headers=headers)
                assert list_resp.status_code == 200
                ids = [j["id"] for j in list_resp.json()]
                assert job_id in ids

                # Delete
                del_resp = await ac.delete(f"/api/cron/{job_id}", headers=headers)
                assert del_resp.status_code == 200