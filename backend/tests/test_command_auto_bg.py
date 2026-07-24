import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.services.tools.executors import execute_command


@pytest.mark.asyncio
async def test_pytest_auto_background(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKTON_FILE_BROWSER_ROOT", str(tmp_path))

    class Item:
        id = "bg-test-1"

    with patch(
        "backend.services.tools.process_registry.start_background",
        new_callable=AsyncMock,
        return_value=Item(),
    ) as sb, patch(
        "backend.services.tools.process_registry.format_process",
        return_value="status=running",
    ):
        res = await execute_command(
            {},
            {"command": "pytest -q", "cwd": str(tmp_path), "timeout": 120},
        )
    assert "Background" in res
    assert "bg-test-1" in res
    sb.assert_awaited()
