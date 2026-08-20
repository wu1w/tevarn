# -*- coding: utf-8 -*-
"""OAuth token refresh must land in runtime llm_api_key, not only catalog.

Regression: Grok OAuth refresh wrote the new JWT to model_catalog, but the
agent loop discarded a stale session snapshot and used the process-wide
LLM singleton still holding the expired key → api.x.ai 403.
"""
from __future__ import annotations

import asyncio

from backend.core import model_catalog as mc
from backend.core.config import settings
from backend.services.llm.factory import (
    LLMServiceFactory,
    follow_global_llm_snapshot,
)


def _oauth_catalog(token: str) -> dict:
    return {
        "version": 2,
        "active_provider_id": "xai-oauth",
        "active_model": "grok-4.6",
        "providers": [
            {
                "id": "xai-oauth",
                "name": "Grok OAuth",
                "llm_provider": "openai-compatible",
                "llm_base_url": "https://api.x.ai/v1",
                "llm_api_key": token,
                "enabled": True,
                "active_model": "grok-4.6",
                "active_credential_id": "cred1",
                "credentials": [
                    {
                        "id": "cred1",
                        "label": "Grok OAuth",
                        "api_key": token,
                        "enabled": True,
                        "auth_mode": "oauth_device_code",
                        "expires_at": "2099-01-01T00:00:00+00:00",
                    }
                ],
            }
        ],
    }


class _FakeRepo:
    def __init__(self) -> None:
        self.rows: dict[str, object] = {}

    async def upsert(self, key, value, category="general", description=None):
        self.rows[key] = value
        return None

    async def get_by_key(self, key):
        return None


def test_catalog_active_api_key_reads_credential():
    cat = _oauth_catalog("fresh-jwt-token")
    assert mc.catalog_active_api_key(cat) == "fresh-jwt-token"


def test_sync_oauth_runtime_noop_when_already_aligned(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "same-token", raising=False)
    repo = _FakeRepo()
    changed = asyncio.run(
        mc.sync_oauth_runtime(
            _oauth_catalog("same-token"),
            repo=repo,
            key_before="same-token",
        )
    )
    assert changed is False
    assert repo.rows == {}


def test_sync_oauth_runtime_applies_when_runtime_diverged(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "old-expired-token", raising=False)
    monkeypatch.setattr(settings, "llm_model", "grok-4.6", raising=False)
    repo = _FakeRepo()
    changed = asyncio.run(
        mc.sync_oauth_runtime(
            _oauth_catalog("new-oauth-token-value"),
            repo=repo,
            key_before="new-oauth-token-value",
        )
    )
    assert changed is True
    assert repo.rows["llm_api_key"] == "new-oauth-token-value"
    assert repo.rows["llm_catalog_provider_id"] == "xai-oauth"
    assert settings.llm_api_key == "new-oauth-token-value"


def test_follow_global_llm_snapshot_replaces_stale(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai-compatible", raising=False)
    monkeypatch.setattr(settings, "llm_model", "grok-4.6", raising=False)
    monkeypatch.setattr(settings, "llm_catalog_provider_id", "xai-oauth", raising=False)
    monkeypatch.setattr(settings, "llm_base_url", "https://api.x.ai/v1", raising=False)
    monkeypatch.setattr(settings, "llm_api_key", "runtime-token", raising=False)

    out = follow_global_llm_snapshot(
        {"provider": "openai-compatible", "model": "", "provider_id": ""}
    )
    assert out["provider"] == "openai-compatible"
    assert out["provider_id"] == "xai-oauth"
    assert out["model"] == "grok-4.6"
    assert out["api_key"] == "runtime-token"


def test_follow_global_llm_snapshot_none_becomes_global(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai-compatible", raising=False)
    monkeypatch.setattr(settings, "llm_model", "grok-4.6", raising=False)
    monkeypatch.setattr(settings, "llm_catalog_provider_id", "xai-oauth", raising=False)
    monkeypatch.setattr(settings, "llm_base_url", "https://api.x.ai/v1", raising=False)
    out = follow_global_llm_snapshot(None)
    assert out["provider_id"] == "xai-oauth"
    assert out.get("provider")


def test_follow_global_llm_snapshot_respects_locked(monkeypatch):
    monkeypatch.setattr(settings, "llm_model", "grok-4.6", raising=False)
    monkeypatch.setattr(settings, "llm_catalog_provider_id", "xai-oauth", raising=False)
    locked = {
        "locked": True,
        "provider": "openai",
        "provider_id": "openai",
        "model": "gpt-4o",
    }
    assert follow_global_llm_snapshot(locked)["model"] == "gpt-4o"


def test_follow_global_llm_snapshot_fills_missing_provider_id(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai-compatible", raising=False)
    monkeypatch.setattr(settings, "llm_model", "grok-4.6", raising=False)
    monkeypatch.setattr(settings, "llm_catalog_provider_id", "xai-oauth", raising=False)
    monkeypatch.setattr(settings, "llm_base_url", "https://api.x.ai/v1", raising=False)
    out = follow_global_llm_snapshot(
        {
            "provider": "openai-compatible",
            "model": "grok-4.6",
            "base_url": "https://api.x.ai/v1",
        }
    )
    assert out["provider_id"] == "xai-oauth"


def test_get_service_for_snapshot_uses_live_catalog_key(monkeypatch):
    monkeypatch.setattr(
        LLMServiceFactory,
        "_resolve_live_credentials",
        classmethod(
            lambda cls, pid, url: {
                "api_key": "fresh-catalog-jwt",
                "base_url": "https://api.x.ai/v1",
                "llm_provider": "openai-compatible",
            }
        ),
    )
    svc = LLMServiceFactory.get_service_for_snapshot(
        {
            "provider": "openai-compatible",
            "provider_id": "xai-oauth",
            "model": "grok-4.6",
            "base_url": "https://api.x.ai/v1",
            "api_key": "stale-runtime-jwt",
        }
    )
    assert svc.api_key == "fresh-catalog-jwt"


def test_looks_like_placeholder_key_masks():
    assert LLMServiceFactory._looks_like_placeholder_key("eyJ0...Lnwg") is True
    assert LLMServiceFactory._looks_like_placeholder_key("") is True
    assert (
        LLMServiceFactory._looks_like_placeholder_key("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")
        is False
    )
