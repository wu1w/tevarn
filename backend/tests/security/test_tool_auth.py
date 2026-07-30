"""工具端点认证 / 信任边界回归测试（Phase 1.1）。

冻结真实行为，取自：
- backend/api/dependencies.py: _is_loopback_host / assert_local_single_user
- backend/core/security.py: create_access_token / decode_access_token

信任边界要点：只信 socket 对端（request.client.host），不信可伪造的
X-Forwarded-For；伪造/无 exp 的 JWT 一律拒绝（返回 None）。
"""

from __future__ import annotations

from types import SimpleNamespace

import jwt  # 项目已从 python-jose 迁移到 PyJWT
import pytest
from fastapi import HTTPException

from backend.api.dependencies import _is_loopback_host, assert_local_single_user
from backend.core.security import (
    ALGORITHM,
    create_access_token,
    decode_access_token,
)

# ── _is_loopback_host：信任边界 ───────────────────────────────────────

@pytest.mark.parametrize(
    "host", ["localhost", "testclient", "127.0.0.1", "127.0.0.5", "::1"]
)
def test_loopback_hosts_trusted(host: str) -> None:
    assert _is_loopback_host(host) is True


@pytest.mark.parametrize("host", ["8.8.8.8", "203.0.113.9", "10.0.0.1", "evil.example.com"])
def test_non_loopback_hosts_untrusted(host: str) -> None:
    assert _is_loopback_host(host) is False


@pytest.mark.parametrize("host", [None, ""])
def test_missing_host_untrusted(host) -> None:
    assert _is_loopback_host(host) is False


# ── assert_local_single_user：免登录仅对本机开放 ─────────────────────

def _fake_request(host: str | None) -> SimpleNamespace:
    return SimpleNamespace(client=SimpleNamespace(host=host) if host is not None else None)


def test_single_user_loopback_allowed() -> None:
    # 测试环境 SINGLE_USER_MODE=True；loopback 对端应放行（不抛异常）
    assert assert_local_single_user(_fake_request("127.0.0.1")) is None


def test_single_user_public_client_forbidden() -> None:
    with pytest.raises(HTTPException) as ei:
        assert_local_single_user(_fake_request("8.8.8.8"))
    assert ei.value.status_code == 403


def test_forwarded_for_header_cannot_spoof_loopback() -> None:
    # 对端是公网 IP，即便伪造 X-Forwarded-For 也无从影响：守卫只看 client.host
    req = _fake_request("203.0.113.7")
    req.headers = {"X-Forwarded-For": "127.0.0.1"}
    with pytest.raises(HTTPException) as ei:
        assert_local_single_user(req)
    assert ei.value.status_code == 403


# ── JWT：伪造/畸形 token 一律拒绝 ─────────────────────────────────────

@pytest.mark.parametrize("token", ["", "garbage", "a.b.c", "Bearer x"])
def test_malformed_token_rejected(token: str) -> None:
    assert decode_access_token(token) is None


def test_valid_token_roundtrips() -> None:
    tok = create_access_token({"sub": "user-123"})
    payload = decode_access_token(tok)
    assert payload is not None and payload["sub"] == "user-123"


def test_forged_token_wrong_secret_rejected() -> None:
    # 用错误密钥签发的 token 必须被拒（签名校验失败）
    forged = jwt.encode(
        {"sub": "attacker", "exp": 9999999999},
        "attacker-secret-not-the-server-one",
        algorithm=ALGORITHM,
    )
    assert decode_access_token(forged) is None


def test_token_without_exp_rejected() -> None:
    # decode 要求 exp 声明；缺失 exp 的 token 视为无效
    from backend.core.security import settings

    no_exp = jwt.encode({"sub": "x"}, settings.jwt_secret, algorithm=ALGORITHM)
    assert decode_access_token(no_exp) is None
