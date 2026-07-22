"""Redo stack + hunk selective apply."""

from __future__ import annotations

from pathlib import Path

import pytest

from takton_code.agent.file_history import FileHistory, make_unified_diff
from takton_code.agent.hunks import apply_selected_hunks, parse_unified_hunks
from takton_code.session.store import SessionStore


def test_parse_and_apply_hunks():
    original = "a\nb\nc\nd\n"
    target = "a\nB\nc\nD\n"
    patch = make_unified_diff("f.txt", original, target)
    hunks = parse_unified_hunks(patch)
    assert len(hunks) >= 1
    # apply all
    all_idx = list(range(len(hunks)))
    out, errs = apply_selected_hunks(original, hunks, all_idx)
    assert out.replace("\r\n", "\n") == target.replace("\r\n", "\n")


@pytest.mark.asyncio
async def test_unrewind_restores_pre_state(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TAKTON_CODE_HOME", str(tmp_path / "home"))
    root = tmp_path / "proj"
    root.mkdir()
    f = root / "a.txt"
    f.write_text("v1\n", encoding="utf-8", newline="\n")
    store = SessionStore(tmp_path / "s.db")
    await store.open()
    sid = await store.create_session(project_root=str(root), title="t")
    hist = FileHistory(store, root, home=tmp_path / "home")
    p = await hist.create_point(sid, label="base", paths=["a.txt"])
    f.write_text("v2\n", encoding="utf-8", newline="\n")
    r = await hist.rewind(sid, p.id)
    assert r["ok"]
    assert f.read_text(encoding="utf-8").replace("\r\n", "\n") == "v1\n"
    assert r.get("redo_id")
    u = await hist.unrewind(sid)
    assert u["ok"]
    assert f.read_text(encoding="utf-8").replace("\r\n", "\n") == "v2\n"
    await store.close()


@pytest.mark.asyncio
async def test_apply_hunks_partial(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TAKTON_CODE_HOME", str(tmp_path / "h2"))
    root = tmp_path / "p2"
    root.mkdir()
    f = root / "m.txt"
    # two separate change regions
    original = "L1\nL2\nL3\nL4\nL5\nL6\n"
    checkpoint = "L1\nX2\nL3\nL4\nY5\nL6\n"
    f.write_text(original, encoding="utf-8", newline="\n")
    store = SessionStore(tmp_path / "s2.db")
    await store.open()
    sid = await store.create_session(project_root=str(root), title="t")
    hist = FileHistory(store, root, home=tmp_path / "h2")
    # create checkpoint content on disk then point
    f.write_text(checkpoint, encoding="utf-8", newline="\n")
    p = await hist.create_point(sid, label="cp", paths=["m.txt"])
    # disk back to original
    f.write_text(original, encoding="utf-8", newline="\n")
    dry = await hist.rewind(sid, p.id, dry_run=True)
    assert dry.get("unified_diffs")
    patch = dry["unified_diffs"][0]["patch"]
    hunks = parse_unified_hunks(patch)
    assert len(hunks) >= 1
    # apply only first hunk
    out = await hist.apply_hunks(sid, "m.txt", [0], patch=patch)
    assert out["ok"]
    text = f.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "X2" in text or text != original
    await store.close()
