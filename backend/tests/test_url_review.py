"""从链接添加：SSRF 与风险分级。"""

from __future__ import annotations

import pytest

from backend.services.skill_store.url_review import (
    _extract_tools,
    _score_risk,
    review_extension_url,
)


def test_extract_tools_from_yamlish():
    content = """
name: demo
tools:
  - web_search
  - shell
permissions: [network]
"""
    tools = _extract_tools(content)
    assert "web_search" in tools
    assert "shell" in tools


def test_score_risk_dangerous_rm():
    risk, findings = _score_risk("run rm -rf /tmp/foo with sudo", ["shell"])
    assert risk in ("high", "dangerous")
    assert findings


@pytest.mark.asyncio
async def test_review_blocks_localhost():
    r = await review_extension_url("http://127.0.0.1/evil.md")
    assert r["ok"] is False
    assert r["risk"] == "dangerous"
    assert "SSRF" in (r.get("error") or "")
