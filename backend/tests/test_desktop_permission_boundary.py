from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from backend.api.routes.desktop import require_native_permission_proof
from backend.core.config import settings
from backend.services.desktop import (
    DesktopAgentService,
    DesktopOperationResult,
    OperationType,
    PermissionLevel,
)


class _FakeAdapter:
    def __init__(self) -> None:
        self.calls = 0

    async def screenshot(self, **_params) -> DesktopOperationResult:
        self.calls += 1
        return DesktopOperationResult(success=True, message="captured")


def test_electron_permission_endpoint_requires_main_process_proof(monkeypatch) -> None:
    monkeypatch.setattr(settings, "desktop_permission_secret", "native-secret")
    with pytest.raises(HTTPException) as exc:
        require_native_permission_proof(None)
    assert exc.value.status_code == 403

    require_native_permission_proof("native-secret")


@pytest.mark.asyncio
async def test_request_cannot_self_assert_desktop_permission(monkeypatch) -> None:
    async def _no_persisted_permission(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "backend.repositories.desktop_permission_repo."
        "AsyncDesktopPermissionRepository.get_permission",
        _no_persisted_permission,
    )

    service = DesktopAgentService()
    adapter = _FakeAdapter()
    service._platform_adapter = adapter
    service._initialized = True
    user_id = uuid.uuid4()

    # A caller-provided enum value is metadata, not proof that approval happened.
    denied = await service.execute_operation(
        user_id=user_id,
        operation=OperationType.SCREENSHOT,
        params={},
        permission=PermissionLevel.ALLOW_ONCE,
    )
    assert denied.success is False
    assert denied.data["requires_permission"] is True
    assert adapter.calls == 0

    await service.set_permission(
        user_id, OperationType.SCREENSHOT, PermissionLevel.ALLOW_ONCE
    )
    allowed = await service.execute_operation(
        user_id=user_id,
        operation=OperationType.SCREENSHOT,
        params={},
    )
    assert allowed.success is True
    assert adapter.calls == 1

    # ALLOW_ONCE is consumed atomically and cannot be replayed.
    replay = await service.execute_operation(
        user_id=user_id,
        operation=OperationType.SCREENSHOT,
        params={},
    )
    assert replay.success is False
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_session_permission_uses_operation_value_key(monkeypatch) -> None:
    async def _no_persisted_permission(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "backend.repositories.desktop_permission_repo."
        "AsyncDesktopPermissionRepository.get_permission",
        _no_persisted_permission,
    )

    service = DesktopAgentService()
    user_id = uuid.uuid4()
    await service.set_permission(
        user_id, OperationType.CLICK, PermissionLevel.ALLOW_SESSION, "Editor"
    )

    allowed, level = await service.check_permission(
        user_id, OperationType.CLICK, "Editor"
    )
    assert allowed is True
    assert level is PermissionLevel.ALLOW_SESSION
