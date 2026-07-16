"""Workspace 绑定：项目根目录 + 树浏览 + 命令执行（专业模式终端）。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 进程内缓存：user_id -> workspace root
_USER_ROOTS: dict[str, str] = {}

# 持久化文件路径（SQLite 之外的最轻量方案）
_PERSIST_PATH = os.environ.get(
    "TAKTON_WORKSPACE_STATE",
    os.path.join(os.path.dirname(__file__), "..", "workspace_state.json"),
)


def _load_persisted() -> dict[str, str]:
    """从磁盘加载持久化的 workspace 绑定"""
    try:
        p = Path(_PERSIST_PATH).expanduser().resolve()
        if p.is_file():
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.debug(f"Failed to load workspace state: {e}")
    return {}


def _persist(roots: dict[str, str]) -> None:
    """将 workspace 绑定持久化到磁盘"""
    try:
        p = Path(_PERSIST_PATH).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(roots, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Failed to persist workspace state: {e}")


# 启动时加载持久化数据
_USER_ROOTS.update(_load_persisted())

# 危险命令粗检（仍允许用户执行，但标记）
_DANGEROUS = re.compile(
    r"\b(rm\s+-rf\s+/|format\s+|mkfs\.|del\s+/f\s+/s\s+|Remove-Item\s+-Recurse\s+-Force\s+[A-Z]:\\)\b",
    re.I,
)


def get_root(user_id: str | None) -> Path | None:
    if not user_id:
        return None
    raw = _USER_ROOTS.get(str(user_id))
    if not raw:
        return None
    p = Path(raw).expanduser()
    try:
        p = p.resolve()
    except OSError:
        return None
    return p if p.is_dir() else None


def set_root(user_id: str, root: str) -> Path:
    p = Path(root).expanduser().resolve()
    if not p.is_dir():
        raise FileNotFoundError(f"Not a directory: {root}")
    _USER_ROOTS[str(user_id)] = str(p)
    _persist(_USER_ROOTS)
    return p


def clear_root(user_id: str) -> None:
    _USER_ROOTS.pop(str(user_id), None)
    _persist(_USER_ROOTS)


def resolve_under_root(root: Path, rel: str = "") -> Path:
    rel = (rel or "").replace("\\", "/").lstrip("/")
    target = (root / rel).resolve() if rel else root
    try:
        target.relative_to(root)
    except ValueError as e:
        raise PermissionError("Path escapes workspace root") from e
    return target


def build_tree(dir_path: Path, root: Path, max_depth: int = 1, depth: int = 0) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if depth > max_depth:
        return items
    try:
        entries = sorted(dir_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    except PermissionError:
        return items

    skip = {".git", "node_modules", "__pycache__", "venv", ".venv", "dist", ".next", "win-unpacked"}
    for entry in entries:
        if entry.name.startswith(".") and entry.name not in {".env.example"}:
            if entry.name not in {".gitignore", ".env.example"}:
                # 仍显示常见配置；隐藏 .git 内容等
                if entry.name in {".git"} or entry.name.startswith(".git"):
                    continue
        if entry.name in skip:
            continue
        is_dir = entry.is_dir()
        try:
            rel = str(entry.relative_to(root)).replace("\\", "/")
        except ValueError:
            continue
        item: dict[str, Any] = {
            "name": entry.name,
            "path": rel,
            "type": "directory" if is_dir else "file",
        }
        if is_dir and depth < max_depth:
            item["children"] = build_tree(entry, root, max_depth, depth + 1)
        if not is_dir:
            try:
                item["size"] = entry.stat().st_size
            except OSError:
                item["size"] = 0
        items.append(item)
    return items


async def exec_command(
    root: Path,
    command: str,
    timeout: float = 120.0,
) -> dict[str, Any]:
    command = (command or "").strip()
    if not command:
        return {"ok": False, "exit_code": -1, "stdout": "", "stderr": "empty command"}

    dangerous = bool(_DANGEROUS.search(command))
    env = os.environ.copy()
    env["TAKTON_WORKSPACE"] = str(root)

    if os.name == "nt":
        # Windows：用 cmd /c，cwd 为项目根
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            shell=True,
        )
    else:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            executable="/bin/bash",
        )

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"timeout after {timeout}s",
            "dangerous": dangerous,
            "cwd": str(root),
        }

    stdout = (stdout_b or b"").decode("utf-8", errors="replace")
    stderr = (stderr_b or b"").decode("utf-8", errors="replace")
    # 截断过大输出
    max_len = 80_000
    if len(stdout) > max_len:
        stdout = stdout[:max_len] + "\n…[truncated]"
    if len(stderr) > max_len:
        stderr = stderr[:max_len] + "\n…[truncated]"

    code = proc.returncode if proc.returncode is not None else -1
    return {
        "ok": code == 0,
        "exit_code": code,
        "stdout": stdout,
        "stderr": stderr,
        "dangerous": dangerous,
        "cwd": str(root),
        "command_id": str(uuid.uuid4()),
    }
