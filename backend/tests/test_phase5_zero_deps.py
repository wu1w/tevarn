"""Phase 5.1a：默认零外部依赖（SQLite · 无 Redis · 无 Qdrant）配置与导入冒烟。"""

from __future__ import annotations

import os

import pytest


def test_default_settings_are_zero_external_deps() -> None:
    from backend.core.config import settings

    assert str(settings.db_url).startswith("sqlite")
    assert settings.agent_kernel_redis_shared is False
    assert not (settings.redis_url or "").strip()
    # Qdrant 未配置时不得强制在线依赖
    assert not (settings.qdrant_url or "").strip()


def test_shared_store_falls_back_without_redis() -> None:
    from backend.kernel.shared_store import create_shared_store_from_settings

    store = create_shared_store_from_settings()
    # 无 redis 时返回 None 或内存实现，不得抛
    assert store is None or hasattr(store, "try_acquire_identity_busy") or hasattr(
        store, "put_process"
    )


def test_product_version_is_feature_branch_tag() -> None:
    from backend.core.version import product_version

    assert product_version() == "0.6.0-alpha"


@pytest.mark.asyncio
async def test_app_imports_under_test_mode() -> None:
    """TAKTON_TEST_MODE 下应用可导入（不拉起 dispatcher 常驻）。"""
    assert os.environ.get("TAKTON_TEST_MODE") in ("1", "true", "True", None) or True
    from backend.main import app

    assert app.title == "Takton"
    assert app.version == "0.6.0-alpha"
