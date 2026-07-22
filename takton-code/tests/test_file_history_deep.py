"""Deep Claude-parity: disk backups, versions, rewind preview, scopes."""

from __future__ import annotations

import pytest
from pathlib import Path

from takton_code.agent.autoloop import parse_test_ok, parse_lint_ok, _err_fingerprint
from takton_code.agent.file_history import FileHistory
from takton_code.config import home_dir
from takton_code.session.store import SessionStore


def test_parse_helpers():
    assert parse_test_ok("exit=0") is True
    assert parse_test_ok("2 failed, 3 passed") is False
    assert parse_lint_ok("exit=0\nAll good") is True
    assert _err_fingerprint("line 10 boom") == _err_fingerprint("line 99 boom")


@pytest.mark.asyncio
async def test_disk_backup_and_preview(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TAKTON_CODE_HOME", str(tmp_path / "home"))
    db = tmp_path / "s.db"
    root = tmp_path / "proj"
    root.mkdir()
    f = root / "a.txt"
    f.write_text("v1", encoding="utf-8")

    store = SessionStore(db)
    await store.open()
    sid = await store.create_session(project_root=str(root), title="t")
    hist = FileHistory(store, root, home=tmp_path / "home")

    p1 = await hist.create_point(sid, label="before", kind="manual", paths=["a.txt"])
    assert p1.file_count == 1
    # disk backup exists
    sess_dir = tmp_path / "home" / "file-history" / sid
    assert sess_dir.is_dir()
    assert any(sess_dir.iterdir())

    f.write_text("v2", encoding="utf-8")
    stats = await hist.get_diff_stats(sid, p1.id)
    assert stats["ok"]
    assert stats["would_restore"] == 1

    prev = await hist.rewind(sid, p1.id, dry_run=True)
    assert prev["dry_run"] is True
    assert prev["diff"]["would_restore"] == 1

    res = await hist.rewind(sid, p1.id, scope="code")
    assert res["ok"]
    assert f.read_text(encoding="utf-8") == "v1"

    # version increments
    f.write_text("v3", encoding="utf-8")
    await hist.snapshot_before_edit(sid, "turnA", "a.txt")
    f.write_text("v4", encoding="utf-8")
    await hist.snapshot_before_edit(sid, "turnB", "a.txt")
    pts = await hist.list_points(sid)
    assert len(pts) >= 2

    exp = await hist.export_point(sid, p1.id, tmp_path / "export")
    assert (exp / "manifest.json").is_file()
    await store.close()


@pytest.mark.asyncio
async def test_rewind_scope_conversation_marker(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TAKTON_CODE_HOME", str(tmp_path / "home2"))
    db = tmp_path / "s2.db"
    root = tmp_path / "proj2"
    root.mkdir()
    store = SessionStore(db)
    await store.open()
    sid = await store.create_session(project_root=str(root), title="t")
    hist = FileHistory(store, root, home=tmp_path / "home2")
    p = await hist.mark_user_leaf(sid, turn_id="turn_x", message_id="msg_abc", label="hi")
    res = await hist.rewind(sid, p.id, scope="conversation")
    assert res["ok"]
    assert res.get("truncate_to_message_id") == "msg_abc"
    await store.close()
