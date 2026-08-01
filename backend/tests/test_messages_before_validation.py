"""GET messages?before= 非法时间必须 400 而非 500。"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from backend.api.routes import messages as messages_mod
    from backend.api.dependencies import get_current_user
    from backend.schemas.user import UserRead

    app = FastAPI()
    app.include_router(messages_mod.router, prefix="/api")

    async def _user():
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        return UserRead(
            id=uuid.uuid4(),
            email="tester@example.com",
            username="tester",
            is_active=True,
            is_superuser=True,
            hashed_password="x",
            last_login_at=now,
            created_at=now,
            updated_at=now,
        )

    app.dependency_overrides[get_current_user] = _user
    return TestClient(app)


def test_invalid_before_returns_400(client):
    sid = uuid.uuid4()
    session = MagicMock()
    session.user_id = None

    msg_repo = MagicMock()
    msg_repo.get_history_before = AsyncMock(
        side_effect=ValueError("invalid before timestamp: not-a-date")
    )

    uow = MagicMock()
    uow.sessions.get_by_id = AsyncMock(return_value=session)
    uow.messages = msg_repo
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=False)

    with patch("backend.api.routes.messages.UnitOfWork", return_value=uow):
        with patch("backend.api.routes.messages.assert_session_owner"):
            r = client.get(
                f"/api/sessions/{sid}/messages",
                params={"before": "not-a-date", "limit": 50},
            )
    assert r.status_code == 400
    detail = r.json().get("detail") or ""
    assert "invalid" in str(detail).lower() or "before" in str(detail).lower()
