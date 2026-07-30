"""审批规则消费 + 能力分级。"""

from __future__ import annotations

import pytest

from backend.kernel.approval_rules import (
    DEFAULT_RULES,
    classify_caps,
    evolution_requires_review,
)


def test_classify_low():
    assert classify_caps(["web_search", "file_read"]) == "low"


def test_classify_high():
    assert classify_caps(["shell", "web_search"]) == "high"
    assert classify_caps(["command"]) == "high"


def test_classify_upgrade():
    assert classify_caps(["custom_tool_xyz"]) == "upgrade"


def test_evolution_always_review():
    assert evolution_requires_review() is True


def test_default_rules_have_auto_low():
    keys = {r["key"] for r in DEFAULT_RULES}
    assert "auto_low_risk" in keys
    assert "review_evolution" in keys


@pytest.mark.asyncio
async def test_should_auto_approve_low_without_settings(monkeypatch):
    from backend.kernel import approval_rules as ar

    async def _defaults():
        return list(DEFAULT_RULES)

    monkeypatch.setattr(ar, "load_approval_rules", _defaults)
    assert await ar.should_auto_approve_escalation(["web_search"]) is True
    assert await ar.should_auto_approve_escalation(["shell"]) is False
