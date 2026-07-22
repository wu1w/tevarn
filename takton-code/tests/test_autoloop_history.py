"""Autoloop + file history checkpoint/rewind."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from takton_code.agent.autoloop import parse_test_ok
from takton_code.agent.file_history import FileHistory
from takton_code.session.store import SessionStore


def test_parse_test_ok():
    assert parse_test_ok("exit=0\nok") is True
    assert parse_test_ok("exit=1\nFAIL") is False
    assert parse_test_ok("") is None


@pytest.mark.asyncio
async def test_file_history_rewind(tmp_path: Path):
    db = tmp_path / "s.db"
    root = tmp_path / "proj"
    root.mkdir()
    f = root / "a.txt"
    f.write_text("v1", encoding="utf-8")

    store = SessionStore(db)
    await store.open()
    sid = await store.create_session(project_root=str(root), title="t")
    hist = FileHistory(store, root)

    p1 = await hist.create_point(sid, label="before", kind="manual", paths=["a.txt"])
    assert p1.file_count == 1
    f.write_text("v2", encoding="utf-8")
    p2 = await hist.create_point(sid, label="mid", kind="manual", paths=["a.txt"])
    f.write_text("v3", encoding="utf-8")

    # rewind last point (mid has v2 content as snapshot of then-current = v2)
    # create_point snapshots CURRENT content at create time
    # so p1 has v1, p2 has v2, disk is v3
    res = await hist.rewind(sid, p1.id)
    assert res["ok"]
    assert f.read_text(encoding="utf-8") == "v1"

    f.write_text("v9", encoding="utf-8")
    res2 = await hist.rewind(sid, p2.id)
    assert res2["ok"]
    assert f.read_text(encoding="utf-8") == "v2"

    pts = await hist.list_points(sid)
    assert len(pts) >= 2
    await store.close()


@pytest.mark.asyncio
async def test_snapshot_before_edit_keeps_first(tmp_path: Path):
    db = tmp_path / "s2.db"
    root = tmp_path / "proj2"
    root.mkdir()
    f = root / "b.py"
    f.write_text("orig", encoding="utf-8")
    store = SessionStore(db)
    await store.open()
    sid = await store.create_session(project_root=str(root), title="t2")
    hist = FileHistory(store, root)
    await hist.snapshot_before_edit(sid, "turn1", "b.py")
    f.write_text("changed", encoding="utf-8")
    # second snapshot same path same turn must keep first
    await hist.snapshot_before_edit(sid, "turn1", "b.py")
    pts = await hist.list_points(sid)
    assert pts
    files = await store.load_history_files(pts[0].id)
    assert files[0]["content"] == "orig"
    res = await hist.rewind(sid, pts[0].id)
    assert f.read_text(encoding="utf-8") == "orig"
    await store.close()
