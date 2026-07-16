"""Git status API - check git status, branches, and diff

仓库路径优先级：
1. 环境变量 TAKTON_GIT_REPO / GIT_REPO_PATH
2. settings.file_browser_root 下若存在 .git
3. 当前工作目录若存在 .git
4. 均不可用则返回 is_repo=false（不抛 500）
"""
import logging
import os
import subprocess
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.core.config import settings
from backend.schemas.user import UserRead
from backend.api.dependencies import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/git", tags=["Git"])


def _resolve_repo_path() -> Optional[Path]:
    """解析可用的 git 仓库根目录；找不到则返回 None（不抛错）"""
    candidates: list[str] = []

    env_path = (
        os.environ.get("TAKTON_GIT_REPO", "").strip()
        or os.environ.get("GIT_REPO_PATH", "").strip()
    )
    if env_path:
        candidates.append(env_path)

    # 桌面/配置的工作区
    fb = (settings.file_browser_root or "").strip()
    if fb:
        candidates.append(fb)

    # 常见相对路径
    candidates.extend([".", "workspace", str(Path.cwd())])

    for raw in candidates:
        try:
            p = Path(raw).expanduser()
            if not p.is_absolute():
                p = (Path.cwd() / p).resolve()
            else:
                p = p.resolve()
            if not p.exists() or not p.is_dir():
                continue
            # 向上查找 .git
            cur = p
            for _ in range(6):
                if (cur / ".git").exists():
                    return cur
                if cur.parent == cur:
                    break
                cur = cur.parent
        except (OSError, RuntimeError) as e:
            logger.debug("Skip git candidate %s: %s", raw, e)
            continue
    return None


def _run_git(args: list[str], repo: Path) -> str:
    """Run a git command and return stdout. Failures return empty string."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=10,
            # Windows: 避免控制台编码问题
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            logger.debug(
                "Git command failed: %s -> %s",
                " ".join(args),
                (result.stderr or "").strip()[:200],
            )
            return ""
        return (result.stdout or "").strip()
    except FileNotFoundError:
        # git 未安装：上层统一处理
        raise
    except subprocess.TimeoutExpired:
        logger.warning("Git command timed out: %s", args)
        return ""
    except OSError as e:
        # 无效目录等 — 不抛 500
        logger.warning("Git OS error: %s", e)
        return ""


def _empty_status(*, reason: str = "not_a_repo") -> dict:
    return {
        "branch": "—",
        "ahead": 0,
        "behind": 0,
        "total_commits": 0,
        "changed_files": [],
        "has_changes": False,
        "is_dirty": False,
        "is_repo": False,
        "reason": reason,
        "repo_path": None,
    }


@router.get("/status")
async def get_git_status(
    current_user: Annotated[UserRead, Depends(get_current_user)] = None,
):
    """Get git status - branch, changes, file list"""
    repo = _resolve_repo_path()
    if repo is None:
        return _empty_status(reason="no_git_repo")

    try:
        branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
    except FileNotFoundError:
        return _empty_status(reason="git_not_installed")

    if not branch:
        return {
            **_empty_status(reason="not_a_repo"),
            "repo_path": str(repo),
        }

    status_output = _run_git(["status", "--short"], repo)
    changed_files = []
    if status_output:
        for line in status_output.split("\n"):
            if line.strip():
                xy = line[:2].strip()
                filepath = line[3:].strip()
                changed_files.append({"status": xy, "file": filepath})

    ahead = behind = 0
    try:
        ahead_behind = _run_git(
            ["rev-list", "--count", "--left-right", f"origin/{branch}...HEAD"],
            repo,
        )
        if ahead_behind and "\t" in ahead_behind:
            parts = ahead_behind.split("\t")
            behind = int(parts[0]) if len(parts) > 0 and parts[0].isdigit() else 0
            ahead = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    except Exception:
        ahead = behind = 0

    total_commits = _run_git(["rev-list", "--count", "HEAD"], repo)

    return {
        "branch": branch or "unknown",
        "ahead": ahead,
        "behind": behind,
        "total_commits": int(total_commits) if total_commits and total_commits.isdigit() else 0,
        "changed_files": changed_files,
        "has_changes": len(changed_files) > 0,
        "is_dirty": any(f.get("status", "") not in ("", "??") for f in changed_files),
        "is_repo": True,
        "repo_path": str(repo),
        "reason": None,
    }


@router.get("/branches")
async def get_git_branches(
    current_user: Annotated[UserRead, Depends(get_current_user)] = None,
):
    """List git branches"""
    repo = _resolve_repo_path()
    if repo is None:
        return []
    try:
        output = _run_git(["branch", "--list"], repo)
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Git not installed")
    branches = []
    if output:
        for line in output.split("\n"):
            line = line.strip()
            if line:
                is_current = line.startswith("* ")
                branches.append({
                    "name": line.lstrip("* ").strip(),
                    "current": is_current,
                })
    return branches


@router.get("/diff")
async def get_git_diff(
    file: str = Query("", description="Optional file path to get diff for"),
    current_user: Annotated[UserRead, Depends(get_current_user)] = None,
):
    """Get git diff - staged + unstaged changes"""
    repo = _resolve_repo_path()
    if repo is None:
        return {"unstaged": "", "staged": "", "has_changes": False, "is_repo": False}

    try:
        args = ["diff"]
        if file:
            # 防止路径穿越：仅允许相对路径片段
            safe = file.replace("\\", "/").lstrip("/")
            if ".." in safe.split("/"):
                raise HTTPException(status_code=400, detail="Invalid file path")
            args.extend(["--", safe])

        diff_output = _run_git(args, repo)

        staged_args = ["diff", "--cached"]
        if file:
            staged_args.extend(["--", file.replace("\\", "/").lstrip("/")])
        staged_output = _run_git(staged_args, repo)
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Git not installed")

    return {
        "unstaged": diff_output,
        "staged": staged_output,
        "has_changes": bool(diff_output or staged_output),
        "is_repo": True,
    }
