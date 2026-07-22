"""Hunk workbench payload builder (unit-testable without Textual run)."""

from __future__ import annotations

from pathlib import Path

import pytest

from takton_code.agent.file_history import FileHistory, make_unified_diff
from takton_code.agent.hunks import parse_unified_hunks
from takton_code.session.store import SessionStore


def build_hunk_payloads(udiffs: list[dict]) -> list[dict]:
    out = []
    for u in udiffs:
        path = str(u.get("path") or "")
        patch = u.get("patch") or ""
        hunks = parse_unified_hunks(patch)
        if hunks:
            out.append({"path": path, "patch": patch, "hunks": hunks})
    return out


@pytest.mark.asyncio
async def test_hunk_workbench_payloads_and_apply(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TAKTON_CODE_HOME", str(tmp_path / "h"))
    root = tmp_path / "proj"
    root.mkdir()
    a = root / "a.txt"
    b = root / "b.txt"
    a.write_text("1\n2\n3\n", encoding="utf-8", newline="\n")
    b.write_text("x\ny\n", encoding="utf-8", newline="\n")
    store = SessionStore(tmp_path / "s.db")
    await store.open()
    sid = await store.create_session(project_root=str(root), title="t")
    hist = FileHistory(store, root, home=tmp_path / "h")
    # checkpoint after "target"
    a.write_text("1\nB\n3\n", encoding="utf-8", newline="\n")
    b.write_text("x\nZ\n", encoding="utf-8", newline="\n")
    p = await hist.create_point(sid, label="cp", paths=["a.txt", "b.txt"])
    # disk dirty
    a.write_text("1\n2\n3\n", encoding="utf-8", newline="\n")
    b.write_text("x\ny\n", encoding="utf-8", newline="\n")
    dry = await hist.rewind(sid, p.id, dry_run=True)
    payloads = build_hunk_payloads(dry.get("unified_diffs") or [])
    assert len(payloads) >= 1
    # apply only first file first hunk
    first = payloads[0]
    idxs = [0]
    out = await hist.apply_hunks(
        sid, first["path"], idxs, patch=first["patch"], push_redo=True
    )
    assert out["ok"]
    # unrewind should restore
    u = await hist.unrewind(sid)
    assert u["ok"]
    await store.close()


def test_collect_applies_logic():
    # simulate selected_map merge
    files = [
        {"path": "a.py", "patch": "p1", "hunks": [0, 1]},
        {"path": "b.py", "patch": "p2", "hunks": [0]},
    ]
    selected_map = {"a.py": {0}, "b.py": {0}}
    applies = []
    for f in files:
        path = f["path"]
        idxs = sorted(selected_map.get(path, set()))
        if idxs:
            applies.append({"path": path, "indices": idxs, "patch": f["patch"]})
    assert applies == [
        {"path": "a.py", "indices": [0], "patch": "p1"},
        {"path": "b.py", "indices": [0], "patch": "p2"},
    ]
