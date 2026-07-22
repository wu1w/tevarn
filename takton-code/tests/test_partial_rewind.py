"""Partial rewind + patch focus."""

from __future__ import annotations

from pathlib import Path

import pytest

from takton_code.agent.file_history import FileHistory, format_rewind_side_panel
from takton_code.session.store import SessionStore


@pytest.mark.asyncio
async def test_partial_rewind_only_one_file(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TAKTON_CODE_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    a = root / "a.txt"
    b = root / "b.txt"
    a.write_text("A1", encoding="utf-8", newline="\n")
    b.write_text("B1", encoding="utf-8", newline="\n")
    store = SessionStore(tmp_path / "s.db")
    await store.open()
    sid = await store.create_session(project_root=str(root), title="t")
    hist = FileHistory(store, root, home=tmp_path / "home")
    p = await hist.create_point(sid, label="base", paths=["a.txt", "b.txt"])
    a.write_text("A2", encoding="utf-8", newline="\n")
    b.write_text("B2", encoding="utf-8", newline="\n")

    res = await hist.rewind(sid, p.id, only_paths=["a.txt"])
    assert res["ok"]
    assert res.get("only_paths") == ["a.txt"]
    assert a.read_text(encoding="utf-8").replace("\r\n", "\n") == "A1"
    # b untouched
    assert b.read_text(encoding="utf-8").replace("\r\n", "\n") == "B2"
    assert any("a.txt" in x for x in (res.get("restored") or []))
    await store.close()


@pytest.mark.asyncio
async def test_focus_index_and_panel(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TAKTON_CODE_HOME", str(tmp_path / "home2"))
    root = tmp_path / "p2"
    root.mkdir()
    for name in ("x.py", "y.py"):
        (root / name).write_text(f"{name}-v1\n", encoding="utf-8", newline="\n")
    store = SessionStore(tmp_path / "s2.db")
    await store.open()
    sid = await store.create_session(project_root=str(root), title="t")
    hist = FileHistory(store, root, home=tmp_path / "home2")
    p = await hist.create_point(sid, label="b", paths=["x.py", "y.py"])
    (root / "x.py").write_text("x-v2\n", encoding="utf-8", newline="\n")
    (root / "y.py").write_text("y-v2\n", encoding="utf-8", newline="\n")
    res = await hist.rewind(sid, p.id, dry_run=True, focus_path="y.py")
    assert res.get("unified_diffs")
    assert res.get("focus_index") == 0 or res["unified_diffs"][0]["path"] == "y.py"
    text = format_rewind_side_panel(res, focus_only=True)
    assert "patch 1/" in text
    assert "y.py" in text
    await store.close()
