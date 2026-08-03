"""CEO dynamic capability grants."""

from __future__ import annotations

import pytest

from backend.agent.grant_store import crew_cap_for_tool
from backend.kernel.cap_requests import (
    list_pending,
    mark_granted_for_identity,
    record_cap_request,
    reset_for_tests,
)


@pytest.fixture(autouse=True)
def _clean():
    reset_for_tests()
    yield
    reset_for_tests()


def test_record_and_list_pending():
    r = record_cap_request(
        identity_id="id-1",
        identity_name="金算",
        tool="command",
        needed_cap="command",
        reason="outside",
        inbox_item_id="job-1",
    )
    assert r["status"] == "pending"
    # de-dupe bumps hits
    r2 = record_cap_request(
        identity_id="id-1",
        identity_name="金算",
        tool="command",
        needed_cap="command",
    )
    assert r2["id"] == r["id"]
    assert r2["hits"] == 2
    items = list_pending()
    assert len(items) == 1


def test_mark_granted():
    record_cap_request(
        identity_id="id-2",
        tool="python",
        needed_cap="command",
    )
    n = mark_granted_for_identity("id-2", caps=["command"], by="test")
    assert n == 1
    assert list_pending() == []


def test_tool_to_cap_map():
    assert crew_cap_for_tool("command") == "command"
    assert crew_cap_for_tool("file_write") == "file_rw"
    assert crew_cap_for_tool("web_search") == "web_search"


def test_auto_grant_eligibility():
    from backend.agent.steward_auto_grant import (
        cap_eligible_for_auto,
        format_pending_grants_brief,
    )

    assert cap_eligible_for_auto("web_search") is True
    assert cap_eligible_for_auto("file_read") is True
    assert cap_eligible_for_auto("command") is True  # high-risk default on
    assert cap_eligible_for_auto("sudo") is False
    assert cap_eligible_for_auto("*") is False

    record_cap_request(
        identity_id="id-3",
        identity_name="工程师",
        tool="git",
        needed_cap="git",
    )
    brief = format_pending_grants_brief()
    assert "待批员工提权" in brief
    assert "工程师" in brief or "git" in brief


def test_steward_prompt_has_grant_policy():
    from backend.agent.workforce_dispatch import steward_orchestration_prompt

    p = steward_orchestration_prompt(contact_name="CEO")
    assert "提权是你的职责" in p
    assert "grant_caps" in p
    assert "禁止" in p and "主人" in p
