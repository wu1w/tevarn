"""files API multi-root sandbox resolution (preview/download 403 fix)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.api.routes import files as files_mod


def test_absolute_under_userdata_workspace(tmp_path, monkeypatch):
    # primary browser root = project-like empty dir
    primary = tmp_path / "project"
    primary.mkdir()
    # electron userData workspace with the artifact
    ud = tmp_path / "Roaming" / "tevarn" / "data" / "workspace"
    (ud / "projects").mkdir(parents=True)
    f = ud / "projects" / "tevarn-audit-handoff.md"
    f.write_text("# handoff\n", encoding="utf-8")

    monkeypatch.setattr(files_mod.settings, "file_browser_root", str(primary), raising=False)
    monkeypatch.setattr(
        files_mod,
        "_electron_userdata_workspace_candidates",
        lambda: [ud],
    )
    monkeypatch.setattr(
        files_mod,
        "_sandbox_root",
        lambda: primary.resolve(),
    )

    target, base = files_mod._resolve_path("sandbox", str(f))
    assert target.resolve() == f.resolve()
    assert base.resolve() == ud.resolve()
    files_mod._check_access(target, base)


def test_absolute_outside_all_roots_denied(tmp_path, monkeypatch):
    primary = tmp_path / "project"
    primary.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("nope", encoding="utf-8")

    monkeypatch.setattr(files_mod.settings, "file_browser_root", str(primary), raising=False)
    monkeypatch.setattr(files_mod, "_sandbox_root", lambda: primary.resolve())
    monkeypatch.setattr(files_mod, "_electron_userdata_workspace_candidates", lambda: [])
    monkeypatch.setattr(
        files_mod,
        "_allowed_sandbox_roots",
        lambda: [primary.resolve()],
    )

    with pytest.raises(HTTPException) as ei:
        files_mod._resolve_path("sandbox", str(outside))
    assert ei.value.status_code == 403


def test_relative_prefers_root_with_file(tmp_path, monkeypatch):
    primary = tmp_path / "project"
    primary.mkdir()
    ud = tmp_path / "workspace"
    (ud / "projects").mkdir(parents=True)
    f = ud / "projects" / "a.md"
    f.write_text("ok", encoding="utf-8")

    monkeypatch.setattr(files_mod, "_sandbox_root", lambda: primary.resolve())
    monkeypatch.setattr(
        files_mod,
        "_allowed_sandbox_roots",
        lambda: [primary.resolve(), ud.resolve()],
    )

    target, base = files_mod._resolve_path("sandbox", "projects/a.md")
    assert target.resolve() == f.resolve()
    assert base.resolve() == ud.resolve()
