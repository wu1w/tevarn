"""notifications list_page 合并查询冒烟（防回归到 3 次 session）。"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_list_page_single_session_shape():
    """list_page 返回 items/total/unread 三件套。"""
    from backend.repositories.notification_repo import AsyncNotificationRepository

    repo = AsyncNotificationRepository.__new__(AsyncNotificationRepository)
    session = MagicMock()
    # counts query
    count_row = MagicMock()
    count_row.total = 3
    count_row.unread = 1
    count_result = MagicMock()
    count_result.one.return_value = count_row
    # list query
    list_result = MagicMock()
    list_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(side_effect=[count_result, list_result])

    repo._get_session = AsyncMock(return_value=session)
    repo._close_session = AsyncMock()

    with patch("backend.repositories.notification_repo.NotificationRead") as NR:
        NR.model_validate.side_effect = lambda n: n
        page = await repo.list_page(uuid.uuid4(), unread_only=False, limit=20, offset=0)

    assert page["total"] == 3
    assert page["unread"] == 1
    assert page["items"] == []
    assert session.execute.await_count == 2  # counts + list，同一 session
    repo._get_session.assert_awaited_once()
