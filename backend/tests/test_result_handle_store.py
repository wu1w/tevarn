"""Session-scoped durable spill handles survive kernel process drop."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.agent.result_handle_store import list_handles, load, put, session_key
from backend.tools.builtins.core_tools import execute_result_load


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(tmp_path)))
    return tmp_path


def test_session_key_strips_and_rejects_paths():
    assert session_key("  33077A05-8019-4ea0  ") == "33077a0580194ea0"
    assert session_key("") == "orphan"
    assert session_key(None) == "orphan"
    assert session_key("../etc") == "orphan"
    assert session_key("a/b") == "orphan"


def test_put_then_load_same_session():
    hid = put("sess-A", "full body here", tool="web_search")
    assert re.fullmatch(r"[0-9a-f]{16}", hid)
    assert load("sess-A", hid) == "full body here"
    live = list_handles("sess-A")
    assert any(h["id"] == hid and h["tool"] == "web_search" for h in live)


def test_put_accepts_kernel_issued_id():
    hid = put("s1", "payload", tool="extract", handle_id="68d7bac7418d4625")
    assert hid == "68d7bac7418d4625"
    assert load("s1", hid) == "payload"


def test_other_session_cannot_load():
    hid = put("sess-a", "secret-a")
    put("sess-b", "secret-b")
    with pytest.raises((KeyError, ValueError)):
        load("sess-b", hid)
    assert load("sess-a", hid) == "secret-a"


async def test_unknown_id_lists_live_handles_via_result_load(monkeypatch):
    hid = put("chat1", "kept", tool="python", handle_id="aaaaaaaabbbbbbbb")

    class DeadKernel:
        def result_load(self, handle, process_id=None):
            raise RuntimeError(f"unknown result handle {handle}")

    monkeypatch.setattr("backend.kernel.get_kernel", lambda: DeadKernel())

    out = await execute_result_load(
        {},
        {
            "id": "deadbeefdeadbeef",
            "_kernel_process_id": "proc-new",
            "_session_id": "chat1",
            # forged — must be ignored
            "session_id": "sess-b",
        },
    )
    assert out.startswith("[Error] unknown result handle deadbeefdeadbeef")
    assert "aaaaaaaabbbbbbbb" in out
    assert "live in this session" in out
    assert hid in out


async def test_unknown_id_none_when_empty_session(monkeypatch):
    class DeadKernel:
        def result_load(self, handle, process_id=None):
            raise RuntimeError(f"unknown result handle {handle}")

    monkeypatch.setattr("backend.kernel.get_kernel", lambda: DeadKernel())
    out = await execute_result_load(
        {},
        {
            "id": "zzzzzzzzzzzzzzzz",
            "_kernel_process_id": "p",
            "_session_id": "empty-sess",
        },
    )
    assert "live in this session: (none)" in out


def test_normalize_broken_kernel_still_emits_handle(monkeypatch):
    from backend.agent import tool_result_contract as trc

    class BrokenK:
        def result_spill(self, *_a, **_k):
            raise RuntimeError("kernel down")

    monkeypatch.setattr("backend.kernel.get_kernel", lambda: BrokenK())
    n = max(trc.SPILL_THRESHOLD, trc.tool_budget("python")) + 50
    text = ("LINE-payload-" * (n // 13 + 4))[:n]
    assert len(text) >= trc.SPILL_THRESHOLD
    out = trc.normalize_tool_result(
        text, tool_name="python", process_id="p1", session_id="sess-norm"
    )
    assert "tool_result_handle" in out
    m = re.search(r"tool_result_handle id=([A-Za-z0-9_-]+)", out)
    assert m, out
    hid = m.group(1)
    assert load("sess-norm", hid) == text


async def test_execute_result_load_survives_new_kernel_process(monkeypatch):
    hid = put("chat-turn", "THE-FULL-BODY\n" + ("x" * 80), tool="mcp_extract")

    class DeadKernel:
        def result_load(self, handle, process_id=None):
            raise RuntimeError(f"unknown result handle {handle}")

    monkeypatch.setattr("backend.kernel.get_kernel", lambda: DeadKernel())
    out = await execute_result_load(
        {},
        {
            "id": hid,
            "_kernel_process_id": "brand-new-proc",
            "_session_id": "chat-turn",
            "offset": 0,
            "max_chars": 2000,
        },
    )
    assert out.startswith(f"[result_load id={hid}")
    assert "THE-FULL-BODY" in out
    assert f"end of result id={hid}" in out


async def test_result_load_still_requires_kernel_process_id():
    hid = put("s", "body", handle_id="ccccccccdddddddd")
    out = await execute_result_load({}, {"id": hid, "_session_id": "s"})
    assert "requires bound process" in out


async def test_result_load_pages_from_session_store(monkeypatch):
    body = "ABCDEFGHIJ" * 20  # 200 chars
    hid = put("pg", body, handle_id="pagepagepagepage")

    class DeadKernel:
        def result_load(self, handle, process_id=None):
            raise RuntimeError(f"unknown result handle {handle}")

    monkeypatch.setattr("backend.kernel.get_kernel", lambda: DeadKernel())
    out = await execute_result_load(
        {},
        {
            "id": hid,
            "_kernel_process_id": "p",
            "_session_id": "pg",
            "offset": 10,
            "max_chars": 500,
        },
    )
    assert "offset=10" in out
    assert body[10:] in out


def test_normalize_result_load_page_stays_inline(tmp_path, monkeypatch):
    """Paging tool output IS the page — 20k result_load must not re-spill."""
    from backend.agent import tool_result_contract as trc

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(tmp_path)))

    def _boom(*_a, **_k):
        raise AssertionError("result_load must not spill")

    class FakeK:
        result_spill = staticmethod(_boom)

    monkeypatch.setattr("backend.kernel.get_kernel", lambda: FakeK())

    body = "[result_load id=abcd offset=0 end=20000 total=20000 page_chars=20000]\n" + (
        "P" * 20_000
    )
    assert len(body) > trc.DEFAULT_TOOL_BUDGET
    assert len(body) > trc.SPILL_THRESHOLD
    out = trc.normalize_tool_result(
        body, tool_name="result_load", process_id="p1", session_id="sess-page"
    )
    assert out == body
    assert "tool_result_handle" not in out
    assert list_handles("sess-page") == []
    # case / whitespace
    out2 = trc.normalize_tool_result(
        body, tool_name="  Result_Load  ", session_id="sess-page"
    )
    assert out2 == body
    # truncate_for_llm must also leave the slice intact
    assert trc.truncate_for_llm("result_load", body, budget=100) == body

