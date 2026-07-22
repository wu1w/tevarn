"""Precise conversation rewind + auto classifier."""

from __future__ import annotations

import pytest
from pathlib import Path

from takton_code.agent.auto_classify import classify_tool_call, apply_auto_classifier
from takton_code.agent.file_history import FileHistory
from takton_code.agent.permissions import PermissionGate, rules_for_profile
from takton_code.session.store import SessionStore


def test_auto_classifier_shell():
    assert classify_tool_call("file_read", {"path": "a.py"}).decision == "allow"
    assert classify_tool_call("run_shell", {"command": "rm -rf /"}).decision == "deny"
    assert classify_tool_call("run_shell", {"command": "sudo apt install x"}).decision == "ask"
    assert classify_tool_call("run_tests", {}).decision == "allow"
    d, _ = apply_auto_classifier("ask", "run_shell", {"command": "ls"}, enabled=True)
    assert d == "allow"
    d2, _ = apply_auto_classifier("ask", "run_shell", {"command": "rm -rf /tmp/x"}, enabled=True)
    assert d2 == "deny"


def test_permission_auto_profile():
    g = PermissionGate(profile="auto", mode="build", rules=rules_for_profile("auto"))
    # benign edit
    assert g.check("edit_file", {"path": "src/a.py"}) == "allow"
    # dangerous shell denied by classifier
    assert g.check("run_shell", {"command": "rm -rf /"}) == "deny"


@pytest.mark.asyncio
async def test_precise_message_row_truncate(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TAKTON_CODE_HOME", str(tmp_path / "home"))
    db = tmp_path / "s.db"
    root = tmp_path / "proj"
    root.mkdir()
    store = SessionStore(db)
    await store.open()
    sid = await store.create_session(project_root=str(root), title="t")

    await store.append_message(sid, "system", "sys")
    r1 = await store.append_message(sid, "user", "u1")
    await store.append_message(sid, "assistant", "a1")
    r2 = await store.append_message(sid, "user", "u2")
    await store.append_message(sid, "assistant", "a2")

    hist = FileHistory(store, root, home=tmp_path / "home")
    p = await hist.mark_user_leaf(
        sid, turn_id="t1", message_id="m1", message_row_id=int(r1), label="u1"
    )
    res = await hist.rewind(sid, p.id, scope="conversation")
    assert res["truncate_to_message_row_id"] == int(r1)

    n = await store.truncate_messages_after(sid, keep_until_id=int(r1))
    assert n >= 2  # dropped a1,u2,a2 at least some
    msgs = await store.load_messages(sid)
    roles = [m["role"] for m in msgs]
    assert roles == ["system", "user"]
    assert msgs[1]["content"] == "u1"
    # r2 gone
    assert await store.get_message_row(int(r2)) is None
    await store.close()
