"""Phase 5.2a：公开暴露面安全配置回归。"""

from __future__ import annotations

from backend.core.config import settings
from backend.core.security_check import collect_security_report


def test_cors_default_not_wildcard() -> None:
    raw = (settings.cors_allowed_origins or "").strip()
    assert raw != "*", "default CORS must not be wildcard"


def test_channel_ingress_limits_configured() -> None:
    assert int(settings.channel_ingress_max_chars) > 0
    assert settings.channel_ingress_strip_nul is True


def test_redis_shared_off_by_default() -> None:
    assert settings.agent_kernel_redis_shared is False


def test_security_report_collects_without_crash() -> None:
    rep = collect_security_report()
    assert rep.results
    ids = {r.id for r in rep.results}
    assert "jwt_secret_strength" in ids
    assert "host_single_user_combo" in ids


def test_jwt_secret_not_empty() -> None:
    assert len(settings.jwt_secret) >= 16
