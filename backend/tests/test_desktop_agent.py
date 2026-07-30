"""Desktop Agent 手动集成脚本（非 CI 门禁）。

历史文件以 test_ 前缀命名，迁入 backend/tests 后会被 pytest 收集并在无头
CI 上失败。以下用例默认 skip；本机有桌面环境时可用：

  pytest backend/tests/test_desktop_agent.py -m desktop -s
"""

from __future__ import annotations

import uuid

import pytest

# 仅显式 -m desktop 时运行（CI 默认不选该 marker）
pytestmark = pytest.mark.desktop


@pytest.fixture(autouse=True)
def _require_desktop_marker(request):
    """未选择 desktop marker 时整文件跳过（兼容未配置 -m 的本地默认收集）。"""
    # 若用户未 -m desktop，仍可能收集到本文件 → 无条件 skip，除非 env 放开
    import os

    if os.environ.get("TAKTON_RUN_DESKTOP_TESTS", "").strip() not in {"1", "true", "yes"}:
        pytest.skip("desktop integration: set TAKTON_RUN_DESKTOP_TESTS=1 to run")


@pytest.mark.asyncio
async def test_desktop_service_initialize_and_tools():
    from backend.services.desktop import OperationType, get_desktop_service
    from backend.services.desktop.task_planner import get_task_planner
    from backend.services.desktop.tools import register_desktop_tools
    from backend.tools.registry import ToolRegistry

    service = get_desktop_service()
    await service.initialize()
    assert service.platform

    result = await service.execute_operation(
        user_id=uuid.uuid4(),
        operation=OperationType.SCREENSHOT,
        params={},
    )
    assert result is not None

    planner = get_task_planner()
    operations = await planner.plan_task("打开记事本")
    assert isinstance(operations, list)

    count = register_desktop_tools(ToolRegistry)
    assert count >= 0

    allowed, _record = await service.check_permission(
        user_id=uuid.uuid4(),
        operation=OperationType.SCREENSHOT,
    )
    assert allowed is True or allowed is False
