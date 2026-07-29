"""0.5.2 policy.decision + 0.6 工单通知 切片测试。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.kernel import AgentKernel, KernelPermissionError


@pytest.fixture
def kernel() -> AgentKernel:
    return AgentKernel()


def test_policy_decision_on_allow_and_deny(kernel: AgentKernel) -> None:
    async def go():
        proc = await kernel.create_process("eng", capabilities=["file_read"])
        await kernel.mediate(proc.id, "tool_call", "file_read")
        with pytest.raises(KernelPermissionError):
            await kernel.mediate(proc.id, "tool_call", "shell")
        return proc

    asyncio.run(go())
    pols = kernel.events(kind="policy.decision")
    assert len(pols) >= 2
    outcomes = {p.detail.get("outcome") for p in pols}
    assert "allow" in outcomes
    assert "deny" in outcomes
    denied = [p for p in pols if p.detail.get("outcome") == "deny"]
    assert any(p.detail.get("target") == "shell" for p in denied)
    for p in pols:
        assert "who" in p.detail
        assert "what" in p.detail
        assert p.detail.get("source") == "kernel"


def test_notify_owner_uses_data_field() -> None:
    """dispatcher 通知写入 Notification.data（非 meta）。"""
    from backend.kernel.dispatcher import WorkforceDispatcher

    created: dict = {}

    class FakeRepo:
        async def create(self, data):
            created.update(data)
            return data

    class FakeUser:
        id = "user-1"

    class FakeUserRepo:
        async def get_by_email(self, email):
            return FakeUser()

    async def go():
        disp = WorkforceDispatcher(
            kernel=AgentKernel(),
            inbox=object(),
            registry=object(),
            session_factory=object(),
        )
        with (
            patch(
                "backend.repositories.notification_repo.AsyncNotificationRepository",
                return_value=FakeRepo(),
            ),
            patch(
                "backend.repositories.user_repo.AsyncUserRepository",
                return_value=FakeUserRepo(),
            ),
        ):
            class Ident:
                id = "id-1"
                name = "工程师"
                user_id = None

            await disp._notify_owner(
                kind="task_complete",
                title="工单完成 · 工程师",
                content="巡检完成",
                identity=Ident(),
                item_id="item-99",
            )

    asyncio.run(go())
    assert created.get("type") == "task_complete"
    assert "meta" not in created
    assert isinstance(created.get("data"), dict)
    assert created["data"].get("identity_name") == "工程师"
    assert created["data"].get("inbox_item_id") == "item-99"
    assert created.get("title", "").startswith("工单完成")


def test_parent_child_budget_reservation(kernel: AgentKernel) -> None:
    """J1 委托预算：子进程预算不得超过父 remaining，且预留扣减。"""
    from backend.kernel import BudgetExceededError

    async def go():
        parent = await kernel.create_process(
            "ceo", capabilities=["file_read", "shell"], token_budget=1000
        )
        child = await kernel.create_process(
            "eng",
            parent_id=parent.id,
            capabilities=["file_read"],
            token_budget=300,
        )
        # 父已预留 300
        assert parent.tokens_used >= 300 or (
            kernel.get_process(parent.id).tokens_used >= 300
        )
        with pytest.raises(BudgetExceededError):
            await kernel.create_process(
                "too_much",
                parent_id=parent.id,
                capabilities=["file_read"],
                token_budget=900,
            )
        return child

    child = asyncio.run(go())
    assert child.token_budget == 300
    assert child.parent_id is not None
