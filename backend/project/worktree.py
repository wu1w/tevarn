"""Git worktree management for isolated coding sessions (Grok-style).

Layout (under main repo root):
  .takton/worktrees/<name>/   ← git worktree path
  .takton/worktrees.json      ← registry metadata (optional cache)

CLI parity with Grok:
  takton-code --worktree [name] [--worktree-ref REF]
  takton-code worktree list|show|rm|gc|add
"""

from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


class WorktreeError(RuntimeError):
    pass


@dataclass
class WorktreeInfo:
    name: str
    path: str
    branch: str | None = None
    head: str | None = None
    locked: bool = False
    prunable: bool = False
    main_repo: str | None = None
    created_at: float | None = None
    session_id: str | None = None
    source: str = "git"  # git | registry

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run_git(args: list[str], cwd: Path, *, check: bool = False) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except FileNotFoundError as e:
        raise WorktreeError("git not found on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise WorktreeError(f"git timed out: {' '.join(args)}") from e
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if check and r.returncode != 0:
        raise WorktreeError(err or out or f"git {' '.join(args)} failed ({r.returncode})")
    return r.returncode, out, err


def find_git_root(start: Path) -> Path | None:
    cur = start.resolve()
    for _ in range(16):
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    # also try git rev-parse
    code, out, _ = _run_git(["rev-parse", "--show-toplevel"], start)
    if code == 0 and out:
        return Path(out).resolve()
    return None


def worktrees_base(main_repo: Path) -> Path:
    return (main_repo / ".takton" / "worktrees").resolve()


def registry_path(main_repo: Path) -> Path:
    return main_repo / ".takton" / "worktrees.json"


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", name.strip()).strip("-._")
    return (s or f"wt-{uuid.uuid4().hex[:8]}")[:64]


def _load_registry(main_repo: Path) -> dict[str, Any]:
    p = registry_path(main_repo)
    if not p.is_file():
        return {"worktrees": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"worktrees": {}}
        data.setdefault("worktrees", {})
        return data
    except (OSError, json.JSONDecodeError):
        return {"worktrees": {}}


def _save_registry(main_repo: Path, data: dict[str, Any]) -> None:
    p = registry_path(main_repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_worktree_list(porcelain: str, main_repo: Path | None = None) -> list[WorktreeInfo]:
    """Parse `git worktree list --porcelain`."""
    items: list[WorktreeInfo] = []
    cur: dict[str, Any] = {}
    for line in porcelain.splitlines():
        if not line.strip():
            if cur.get("path"):
                items.append(_info_from_block(cur, main_repo))
            cur = {}
            continue
        if line.startswith("worktree "):
            if cur.get("path"):
                items.append(_info_from_block(cur, main_repo))
            cur = {"path": line[len("worktree ") :].strip()}
        elif line.startswith("HEAD "):
            cur["head"] = line[len("HEAD ") :].strip()
        elif line.startswith("branch "):
            ref = line[len("branch ") :].strip()
            cur["branch"] = ref.replace("refs/heads/", "") if ref.startswith("refs/") else ref
        elif line == "detached":
            cur["branch"] = None
            cur["detached"] = True
        elif line == "locked" or line.startswith("locked "):
            cur["locked"] = True
        elif line == "prunable" or line.startswith("prunable "):
            cur["prunable"] = True
    if cur.get("path"):
        items.append(_info_from_block(cur, main_repo))
    return items


def _info_from_block(cur: dict[str, Any], main_repo: Path | None) -> WorktreeInfo:
    path = Path(cur["path"]).resolve()
    name = path.name
    base = worktrees_base(main_repo) if main_repo else None
    if base and path.parent.resolve() == base:
        name = path.name
    elif main_repo and path.resolve() == main_repo.resolve():
        name = "(main)"
    return WorktreeInfo(
        name=name,
        path=str(path),
        branch=cur.get("branch"),
        head=(cur.get("head") or "")[:12] or None,
        locked=bool(cur.get("locked")),
        prunable=bool(cur.get("prunable")),
        main_repo=str(main_repo) if main_repo else None,
        source="git",
    )


def list_worktrees(repo_path: str | Path) -> list[WorktreeInfo]:
    root = find_git_root(Path(repo_path).expanduser().resolve())
    if not root:
        raise WorktreeError(f"not a git repository: {repo_path}")
    code, out, err = _run_git(["worktree", "list", "--porcelain"], root)
    if code != 0:
        raise WorktreeError(err or "git worktree list failed")
    items = parse_worktree_list(out, root)
    # merge registry metadata
    reg = _load_registry(root).get("worktrees") or {}
    for it in items:
        meta = reg.get(it.name) or reg.get(it.path) or {}
        if isinstance(meta, dict):
            it.created_at = meta.get("created_at")
            it.session_id = meta.get("session_id")
            if meta.get("branch") and not it.branch:
                it.branch = meta.get("branch")
    return items


def show_worktree(repo_path: str | Path, name_or_path: str) -> WorktreeInfo:
    items = list_worktrees(repo_path)
    key = name_or_path.strip().replace("\\", "/")
    for it in items:
        if it.name == name_or_path or it.path.replace("\\", "/") == key:
            return it
        if Path(it.path).name == name_or_path:
            return it
    raise WorktreeError(f"worktree not found: {name_or_path}")


def add_worktree(
    repo_path: str | Path,
    name: str | None = None,
    *,
    ref: str | None = None,
    new_branch: bool = True,
    session_id: str | None = None,
    force: bool = False,
) -> WorktreeInfo:
    """Create a linked worktree under .takton/worktrees/<name>."""
    root = find_git_root(Path(repo_path).expanduser().resolve())
    if not root:
        raise WorktreeError(f"not a git repository: {repo_path}")

    wt_name = _slugify(name or f"tkc-{time.strftime('%Y%m%d-%H%M%S')}")
    base = worktrees_base(root)
    base.mkdir(parents=True, exist_ok=True)
    # ensure .takton/worktrees is gitignored at repo level if possible
    _ensure_gitignore(root)

    dest = base / wt_name
    if dest.exists() and not force:
        raise WorktreeError(f"worktree path already exists: {dest}")

    branch_name = f"tkc/{wt_name}"
    args: list[str] = ["worktree", "add"]
    if force:
        args.append("--force")

    # base commit
    start_ref = ref or "HEAD"
    # validate ref
    code, _, err = _run_git(["rev-parse", "--verify", start_ref], root)
    if code != 0:
        raise WorktreeError(f"invalid ref {start_ref!r}: {err}")

    if new_branch:
        # if branch exists, check it out in worktree; else -b
        code_b, _, _ = _run_git(["show-ref", "--verify", f"refs/heads/{branch_name}"], root)
        if code_b == 0:
            args += [str(dest), branch_name]
        else:
            args += ["-b", branch_name, str(dest), start_ref]
    else:
        args += ["--detach", str(dest), start_ref]

    code, out, err = _run_git(args, root)
    if code != 0:
        raise WorktreeError(err or out or "git worktree add failed")

    info = show_worktree(root, wt_name)
    # registry
    reg = _load_registry(root)
    reg.setdefault("worktrees", {})[wt_name] = {
        "name": wt_name,
        "path": info.path,
        "branch": info.branch or branch_name,
        "ref": start_ref,
        "created_at": time.time(),
        "session_id": session_id,
        "main_repo": str(root),
    }
    _save_registry(root, reg)
    info.created_at = time.time()
    info.session_id = session_id
    info.main_repo = str(root)
    return info


def remove_worktree(
    repo_path: str | Path,
    name_or_path: str,
    *,
    force: bool = False,
    delete_branch: bool = False,
) -> str:
    root = find_git_root(Path(repo_path).expanduser().resolve())
    if not root:
        raise WorktreeError(f"not a git repository: {repo_path}")
    info = show_worktree(root, name_or_path)
    if info.name == "(main)" or Path(info.path).resolve() == root.resolve():
        raise WorktreeError("refusing to remove main worktree")

    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(info.path)
    code, out, err = _run_git(args, root)
    if code != 0:
        # try force once more if dirty
        if not force:
            code2, out2, err2 = _run_git(["worktree", "remove", "--force", info.path], root)
            if code2 != 0:
                raise WorktreeError(err2 or err or "remove failed")
        else:
            raise WorktreeError(err or out or "remove failed")

    branch = info.branch
    if delete_branch and branch and branch.startswith("tkc/"):
        _run_git(["branch", "-D", branch], root)

    reg = _load_registry(root)
    reg.get("worktrees", {}).pop(info.name, None)
    # also pop by path keys
    for k, v in list((reg.get("worktrees") or {}).items()):
        if isinstance(v, dict) and v.get("path") == info.path:
            reg["worktrees"].pop(k, None)
    _save_registry(root, reg)
    return f"removed worktree {info.name} ({info.path})"


def gc_worktrees(repo_path: str | Path) -> list[str]:
    """Prune stale worktree metadata and remove empty/orphan tkc worktrees."""
    root = find_git_root(Path(repo_path).expanduser().resolve())
    if not root:
        raise WorktreeError(f"not a git repository: {repo_path}")
    msgs: list[str] = []
    code, out, err = _run_git(["worktree", "prune", "-v"], root)
    if out:
        msgs.append(out)
    if code != 0 and err:
        msgs.append(err)

    # drop registry entries whose path is gone
    reg = _load_registry(root)
    changed = False
    for k, v in list((reg.get("worktrees") or {}).items()):
        path = Path(v.get("path") or "")
        if not path.exists():
            reg["worktrees"].pop(k, None)
            changed = True
            msgs.append(f"registry: dropped missing {k}")
    if changed:
        _save_registry(root, reg)

    # prune empty dirs under .takton/worktrees
    base = worktrees_base(root)
    if base.is_dir():
        for child in list(base.iterdir()):
            if child.is_dir() and not any(child.iterdir()):
                try:
                    child.rmdir()
                    msgs.append(f"removed empty dir {child.name}")
                except OSError:
                    pass
    return msgs or ["gc: nothing to do"]


def resolve_session_root(
    path: str | Path | None,
    *,
    worktree: str | bool | None = None,
    worktree_ref: str | None = None,
    session_id: str | None = None,
) -> tuple[Path, WorktreeInfo | None, Path]:
    """Resolve (active_root, worktree_info|None, main_repo).

    worktree:
      None/False → use bound project root
      True/"" → create auto-named worktree
      "name" → use existing or create named worktree
    """
    start = Path(path or Path.cwd()).expanduser().resolve()
    if start.is_file():
        start = start.parent
    main = find_git_root(start)
    if not main:
        # non-git: no worktree
        if worktree:
            raise WorktreeError("worktree requires a git repository")
        return start, None, start

    if not worktree and worktree is not True:
        # still return main binding from start
        return start if start == main or main in start.parents or start in [main] else start, None, main

    # worktree requested
    name: str | None
    if worktree is True or worktree == "" or worktree is None:
        name = None
    else:
        name = str(worktree)

    # try existing by name
    if name:
        try:
            info = show_worktree(main, name)
            return Path(info.path), info, main
        except WorktreeError:
            pass

    info = add_worktree(
        main,
        name=name,
        ref=worktree_ref,
        new_branch=True,
        session_id=session_id,
    )
    return Path(info.path), info, main


def _ensure_gitignore(main_repo: Path) -> None:
    gi = main_repo / ".gitignore"
    line = ".takton/worktrees/"
    try:
        if gi.is_file():
            text = gi.read_text(encoding="utf-8", errors="replace")
            if line not in text and ".takton/" not in text:
                with gi.open("a", encoding="utf-8") as f:
                    if not text.endswith("\n"):
                        f.write("\n")
                    f.write(f"\n# Takton Code worktrees\n{line}\n")
        else:
            # don't force-create .gitignore at repo root silently if missing — only when .takton exists
            pass
    except OSError:
        pass


def inspect_worktree_state(repo_path: str | Path) -> dict[str, Any]:
    root = find_git_root(Path(repo_path).expanduser().resolve())
    if not root:
        return {"is_git": False, "worktrees": []}
    try:
        items = list_worktrees(root)
    except WorktreeError as e:
        return {"is_git": True, "main_repo": str(root), "error": str(e), "worktrees": []}
    return {
        "is_git": True,
        "main_repo": str(root),
        "worktrees_base": str(worktrees_base(root)),
        "count": len(items),
        "worktrees": [i.to_dict() for i in items],
        "registry": _load_registry(root),
    }
