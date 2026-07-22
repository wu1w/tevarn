"""Worktree unit tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from takton_code.project.worktree import (
    WorktreeError,
    add_worktree,
    gc_worktrees,
    list_worktrees,
    parse_worktree_list,
    remove_worktree,
    resolve_session_root,
    show_worktree,
)


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def test_parse_porcelain():
    sample = """worktree /main
HEAD abcdef1234567890
branch refs/heads/main

worktree /main/.takton/worktrees/feat
HEAD 1234567890abcdef
branch refs/heads/tkc/feat

"""
    items = parse_worktree_list(sample, Path("/main"))
    assert len(items) == 2
    assert items[0].branch == "main"
    assert items[1].name == "feat"


def test_add_list_remove(tmp_path: Path):
    repo = _git_repo(tmp_path)
    info = add_worktree(repo, name="feat-a")
    assert info.name == "feat-a"
    assert Path(info.path).is_dir()
    assert (Path(info.path) / "README.md").is_file()
    assert info.branch and info.branch.startswith("tkc/")

    items = list_worktrees(repo)
    names = {i.name for i in items}
    assert "feat-a" in names
    assert "(main)" in names or any(Path(i.path).resolve() == repo.resolve() for i in items)

    shown = show_worktree(repo, "feat-a")
    assert shown.path == info.path

    msg = remove_worktree(repo, "feat-a", force=True, delete_branch=True)
    assert "removed" in msg
    names2 = {i.name for i in list_worktrees(repo)}
    assert "feat-a" not in names2


def test_resolve_session_root_auto(tmp_path: Path):
    repo = _git_repo(tmp_path)
    active, info, main = resolve_session_root(repo, worktree=True)
    assert main.resolve() == repo.resolve()
    assert info is not None
    assert active == Path(info.path)
    assert active.is_dir()
    # cleanup
    remove_worktree(repo, info.name, force=True, delete_branch=True)


def test_gc(tmp_path: Path):
    repo = _git_repo(tmp_path)
    info = add_worktree(repo, name="tmp-gc")
    remove_worktree(repo, info.name, force=True)
    msgs = gc_worktrees(repo)
    assert isinstance(msgs, list)


def test_bind_project_worktree(tmp_path: Path):
    from takton_code.project.binder import bind_project

    repo = _git_repo(tmp_path)
    ctx = bind_project(repo, worktree="bind-wt")
    assert ctx.is_worktree
    assert ctx.worktree_name == "bind-wt"
    assert Path(ctx.root).name == "bind-wt"
    assert ctx.main_repo and Path(ctx.main_repo).resolve() == repo.resolve()
    remove_worktree(repo, "bind-wt", force=True, delete_branch=True)


def test_not_git_raises(tmp_path: Path):
    with pytest.raises(WorktreeError):
        add_worktree(tmp_path, name="x")
