"""命令权限策略测试（权限控制台三态，2026-07-26）

零 mock：真实 execute_command + 真实 tmp DB（通过 settings 表写策略）。
"""

import json

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from backend.core.command_policy import (
    DEFAULT_ACTION,
    POLICY_SETTING_KEY,
    _sanitize,
    invalidate_command_policy_cache,
    load_command_policy,
)
from backend.main import app
from backend.services.tools.executors import (
    COMMAND_CATEGORIES,
    _DANGEROUS_PATTERNS,
    _LABEL_TO_CATEGORY,
    execute_command,
)


# ---------- 分类完整性 ----------


def test_every_pattern_has_category():
    """每条危险规则的 label 都必须有分类映射（否则三态控制漏管）。"""
    labels = {label for _, label in _DANGEROUS_PATTERNS}
    unmapped = labels - set(_LABEL_TO_CATEGORY)
    assert not unmapped, f"未分类的危险规则: {unmapped}"
    # 映射目标必须是合法分类
    assert set(_LABEL_TO_CATEGORY.values()) <= set(COMMAND_CATEGORIES)


def test_sanitize_filters_invalid():
    policy = _sanitize(
        {"categories": {"delete": "deny", "bogus_cat": "allow", "disk": "bogus_action"}}
    )
    assert policy["delete"] == "deny"
    assert "bogus_cat" not in policy
    assert policy["disk"] == DEFAULT_ACTION  # 非法动作回默认
    # 全部 8 类都有值
    assert set(policy) == set(COMMAND_CATEGORIES)


# ---------- 三态执行行为（真实 execute_command）----------


async def _set_policy(db_session, categories: dict):
    from backend.repositories.setting_repo import AsyncSettingRepository

    repo = AsyncSettingRepository()
    await repo.upsert(
        key=POLICY_SETTING_KEY,
        value=json.dumps({"categories": categories}),
        category="security",
    )
    invalidate_command_policy_cache()


async def _clear_policy():
    from backend.repositories.setting_repo import AsyncSettingRepository

    try:
        await AsyncSettingRepository().delete(POLICY_SETTING_KEY)
    except Exception:
        pass
    invalidate_command_policy_cache()


@pytest.fixture(autouse=True)
async def _policy_cleanup():
    """每个用例前后保证策略干净（repo 走全局库，必须显式清理）。"""
    await _clear_policy()
    yield
    await _clear_policy()


async def test_deny_category_hard_blocks(db_session, tmp_path):
    """deny：直接 [Policy Blocked]，不进入确认流程。"""
    await _set_policy(db_session, {"privilege": "deny"})
    res = await execute_command(
        {"base_path": str(tmp_path)},
        {"command": "sudo ls", "cwd": str(tmp_path)},
    )
    assert "[Policy Blocked]" in res
    assert "privilege" in res
    invalidate_command_policy_cache()


async def test_allow_category_passes_through(db_session, tmp_path):
    """allow：不弹确认直接执行。"""
    await _set_policy(db_session, {"privilege": "allow"})
    res = await execute_command(
        {"base_path": str(tmp_path)},
        # sudo -n true 在无 root 时会失败，但关键是"被执行了"而非被策略拦截
        {"command": "sudo -n echo allowed_test_marker || echo allowed_fallback", "cwd": str(tmp_path)},
    )
    assert "[Policy Blocked]" not in res
    assert "[Denied]" not in res
    assert "allowed_" in res
    invalidate_command_policy_cache()


async def test_default_is_confirm(db_session):
    """未配置任何策略时：所有分类默认 confirm。"""
    invalidate_command_policy_cache()
    policy = await load_command_policy(force=True)
    assert set(policy) == set(COMMAND_CATEGORIES)
    assert all(v == "confirm" for v in policy.values())


# ---------- 端点 ----------


async def test_command_policy_endpoint(db_session):
    async with LifespanManager(app) as manager:
        transport = ASGITransport(app=manager.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/settings/security/command-policy")
            assert resp.status_code == 200
            data = resp.json()
            assert data["actions"] == ["allow", "confirm", "deny"]
            cats = {c["id"] for c in data["categories"]}
            assert cats == set(COMMAND_CATEGORIES)
            for c in data["categories"]:
                assert c["action"] in ("allow", "confirm", "deny")
                assert c["name"]
