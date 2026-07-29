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
