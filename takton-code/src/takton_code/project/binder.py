"""Project root discovery and coding context bundle."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CODE_MD_TEMPLATE = """# Takton Code — project conventions

## Build & test
- Test command: `{test_cmd}`
- Lint command: `{lint_cmd}`

## Rules
- Prefer small, reviewable diffs
- Do not commit secrets
- Run tests after behavioral changes
- Match existing code style

## Forbidden
- Do not modify secrets / .env with real credentials
- Do not force-push main/master
"""


@dataclass
class ProjectContext:
    root: Path
    is_git: bool = False
    branch: str | None = None
    remote: str | None = None
    agents_md: str | None = None
    claude_md: str | None = None
    code_md: str | None = None
    readme_excerpt: str | None = None
    copilot_md: str | None = None
    cursor_rules: str | None = None
    test_command: str | None = None
    lint_command: str | None = None
    languages: list[str] = field(default_factory=list)
    recent_commits: list[str] = field(default_factory=list)
    package_hints: dict[str, Any] = field(default_factory=dict)
    # worktree
    main_repo: Path | None = None
    worktree_name: str | None = None
    worktree_path: str | None = None
    is_worktree: bool = False

    def to_inspect(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "is_git": self.is_git,
            "branch": self.branch,
            "remote": self.remote,
            "languages": self.languages,
            "test_command": self.test_command,
            "lint_command": self.lint_command,
            "has_agents_md": bool(self.agents_md),
            "has_claude_md": bool(self.claude_md),
            "has_code_md": bool(self.code_md),
            "recent_commits": self.recent_commits,
            "package_hints": self.package_hints,
            "main_repo": str(self.main_repo) if self.main_repo else None,
            "is_worktree": self.is_worktree,
            "worktree_name": self.worktree_name,
            "worktree_path": self.worktree_path,
        }

    def prompt_block(self, max_chars: int = 12000) -> str:
        parts = [
            f"PROJECT_ROOT={self.root}",
            f"GIT_BRANCH={self.branch or 'n/a'}",
            f"LANGUAGES={', '.join(self.languages) or 'unknown'}",
            f"TEST_COMMAND={self.test_command or '(not detected)'}",
            f"LINT_COMMAND={self.lint_command or '(not detected)'}",
        ]
        if self.is_worktree:
            parts.append(f"WORKTREE={self.worktree_name} path={self.worktree_path}")
            parts.append(f"MAIN_REPO={self.main_repo}")
            parts.append(
                "You are inside an isolated git worktree. Prefer committing on this branch. "
                "Do not remove the worktree unless the user asks."
            )
        if self.recent_commits:
            parts.append("RECENT_COMMITS:\n" + "\n".join(f"- {c}" for c in self.recent_commits))
        for title, body in (
            ("CODE.md", self.code_md),
            ("AGENTS.md", self.agents_md),
            ("CLAUDE.md", self.claude_md),
            ("COPILOT", getattr(self, "copilot_md", None)),
            ("CURSORRULES", getattr(self, "cursor_rules", None)),
            ("README", self.readme_excerpt),
        ):
            if body:
                parts.append(f"### {title}\n{body}")
        text = "\n\n".join(parts)
        if len(text) > max_chars:
            return text[: max_chars - 20] + "\n…[truncated]"
        return text


def find_git_root(start: Path) -> Path | None:
    cur = start.resolve()
    for _ in range(12):
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def _run_git(args: list[str], cwd: Path) -> str:
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
        if r.returncode != 0:
            return ""
        return (r.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _read_text(path: Path, limit: int = 24_000) -> str | None:
    if not path.is_file():
        return None
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if len(data) > limit:
        return data[: limit - 20] + "\n…[truncated]"
    return data


def _detect_stack(root: Path) -> tuple[list[str], str | None, str | None, dict[str, Any]]:
    langs: list[str] = []
    test_cmd: str | None = None
    lint_cmd: str | None = None
    hints: dict[str, Any] = {}

    pyproject = root / "pyproject.toml"
    req = root / "requirements.txt"
    if pyproject.is_file() or req.is_file() or any(root.glob("*.py")):
        langs.append("python")
        # prefer pytest
        if (root / "tests").is_dir() or any(root.glob("test_*.py")):
            test_cmd = "python -m pytest -q"
        lint_cmd = "python -m compileall -q ."
        if pyproject.is_file():
            hints["pyproject"] = True

    pkg = root / "package.json"
    if pkg.is_file():
        langs.append("javascript")
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            scripts = data.get("scripts") or {}
            hints["package_scripts"] = list(scripts.keys())[:20]
            if "test" in scripts:
                test_cmd = test_cmd or "npm test"
            if "lint" in scripts:
                lint_cmd = lint_cmd or "npm run lint"
            if (root / "tsconfig.json").is_file():
                langs.append("typescript")
                lint_cmd = lint_cmd or "npx tsc --noEmit"
        except (OSError, json.JSONDecodeError):
            pass

    if (root / "go.mod").is_file():
        langs.append("go")
        test_cmd = test_cmd or "go test ./..."

    if (root / "Cargo.toml").is_file():
        langs.append("rust")
        test_cmd = test_cmd or "cargo test"

    # CODE.md override
    code_md = root / ".takton" / "CODE.md"
    if code_md.is_file():
        text = _read_text(code_md) or ""
        for line in text.splitlines():
            low = line.lower().strip()
            if low.startswith("- test command:"):
                test_cmd = line.split(":", 1)[-1].strip().strip("`") or test_cmd
            if low.startswith("- lint command:"):
                lint_cmd = line.split(":", 1)[-1].strip().strip("`") or lint_cmd

    return langs, test_cmd, lint_cmd, hints


def bind_project(
    path: str | Path | None = None,
    *,
    worktree: str | bool | None = None,
    worktree_ref: str | None = None,
    session_id: str | None = None,
) -> ProjectContext:
    """Bind project root. Optionally create/enter a git worktree (Grok-style)."""
    start = Path(path or os.getcwd()).expanduser().resolve()
    if not start.exists():
        raise FileNotFoundError(f"path not found: {start}")
    if start.is_file():
        start = start.parent

    wt_info = None
    main_repo: Path | None = None
    if worktree is not None and worktree is not False:
        from takton_code.project.worktree import WorktreeError, resolve_session_root

        try:
            active, wt_info, main_repo = resolve_session_root(
                start,
                worktree=worktree,
                worktree_ref=worktree_ref,
                session_id=session_id,
            )
            start = active
        except WorktreeError:
            raise

    git_root = find_git_root(start)
    root = git_root or start
    ctx = ProjectContext(root=root, is_git=git_root is not None)

    if main_repo is None and ctx.is_git:
        # detect linked worktree → main common dir
        main_repo = _detect_main_repo(root)

    if ctx.is_git:
        ctx.branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], root) or None
        ctx.remote = _run_git(["config", "--get", "remote.origin.url"], root) or None
        log = _run_git(["log", "--oneline", "-5"], root)
        ctx.recent_commits = [ln for ln in log.splitlines() if ln.strip()][:5]

    ctx.main_repo = main_repo or (root if ctx.is_git else None)
    if wt_info is not None:
        ctx.is_worktree = True
        ctx.worktree_name = wt_info.name
        ctx.worktree_path = wt_info.path
        ctx.main_repo = Path(wt_info.main_repo) if wt_info.main_repo else ctx.main_repo
    elif main_repo and main_repo.resolve() != root.resolve():
        ctx.is_worktree = True
        ctx.worktree_name = root.name
        ctx.worktree_path = str(root)

    # persona files: prefer worktree, fall back to main repo
    search_roots = [root]
    if ctx.main_repo and Path(ctx.main_repo).resolve() != root.resolve():
        search_roots.append(Path(ctx.main_repo))

    for r in search_roots:
        if not ctx.agents_md:
            ctx.agents_md = _read_text(r / "AGENTS.md") or _read_text(r / "agents.md")
        if not ctx.claude_md:
            ctx.claude_md = _read_text(r / "CLAUDE.md") or _read_text(r / "claude.md")
        if not ctx.code_md:
            ctx.code_md = _read_text(r / ".takton" / "CODE.md")
        if not ctx.readme_excerpt:
            ctx.readme_excerpt = _read_text(r / "README.md", limit=4000)
        if not ctx.copilot_md:
            ctx.copilot_md = _read_text(r / ".github" / "copilot-instructions.md", limit=8000)
        if not ctx.cursor_rules:
            ctx.cursor_rules = (
                _read_text(r / ".cursorrules", limit=8000)
                or _read_text(r / ".cursor" / "rules", limit=8000)
            )

    langs, test_cmd, lint_cmd, hints = _detect_stack(root)
    if not langs and ctx.main_repo and Path(ctx.main_repo) != root:
        langs, test_cmd, lint_cmd, hints = _detect_stack(Path(ctx.main_repo))
    ctx.languages = langs
    ctx.test_command = test_cmd
    ctx.lint_command = lint_cmd
    ctx.package_hints = hints
    return ctx


def _detect_main_repo(root: Path) -> Path | None:
    """If root is a linked worktree, return main worktree path."""
    git_path = root / ".git"
    if git_path.is_file():
        try:
            text = git_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return None
        # gitdir: /path/to/main/.git/worktrees/name
        if text.lower().startswith("gitdir:"):
            gitdir = Path(text.split(":", 1)[1].strip())
            try:
                if gitdir.name and gitdir.parent.name == "worktrees":
                    main_git = gitdir.parent.parent  # repo/.git
                    if main_git.name == ".git":
                        return main_git.parent.resolve()
            except Exception:
                return None
    out = _run_git(["rev-parse", "--path-format=absolute", "--git-common-dir"], root)
    if out:
        common = Path(out.strip())
        if common.name == ".git":
            return common.parent.resolve()
        # sometimes returns the git dir path without name check on Windows
        if common.exists() and (common / "HEAD").exists():
            # common is .git
            return common.parent.resolve() if common.name == ".git" else common.resolve()
    return root if (root / ".git").exists() else None


def init_project_files(root: Path, test_cmd: str | None = None, lint_cmd: str | None = None) -> Path:
    """Write .takton/CODE.md template if missing."""
    d = root / ".takton"
    d.mkdir(parents=True, exist_ok=True)
    code_md = d / "CODE.md"
    if not code_md.exists():
        code_md.write_text(
            CODE_MD_TEMPLATE.format(
                test_cmd=test_cmd or "python -m pytest -q",
                lint_cmd=lint_cmd or "python -m compileall -q .",
            ),
            encoding="utf-8",
        )
    return code_md
