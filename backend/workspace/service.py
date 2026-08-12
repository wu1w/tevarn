"""Workspace 绑定服务：用户级项目根、目录树、在根下执行命令。"""

from __future__ import annotations

from backend.core.config import get_tevarn_home

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# user_id -> absolute root path
_ROOTS: dict[str, Path] = {}

_STATE_FILE = Path(
    os.environ.get("TEVARN_WORKSPACE_STATE", "")
    or str(get_tevarn_home() / "workspace_roots.json")
)


def _load_state() -> None:
    global _ROOTS
    if _ROOTS:
        return
    try:
        if _STATE_FILE.is_file():
            data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    p = Path(str(v)).expanduser().resolve()
                    if p.is_dir():
                        _ROOTS[str(k)] = p
    except Exception as e:
        logger.debug("workspace state load: %s", e)


def _save_state() -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: str(v) for k, v in _ROOTS.items()}
        _STATE_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.debug("workspace state save: %s", e)


def get_root(user_id: str) -> Path | None:
    _load_state()
    return _ROOTS.get(str(user_id))


def list_roots() -> dict[str, Path]:
    """返回所有已绑定的 user_id → root。"""
    _load_state()
    return dict(_ROOTS)


def get_any_root() -> Path | None:
    """单用户/无明确 user 时：取任意一个已绑定根（优先 default）。"""
    _load_state()
    if "default" in _ROOTS:
        return _ROOTS["default"]
    if _ROOTS:
        key = sorted(_ROOTS.keys())[0]
        return _ROOTS[key]
    return None



def set_root(user_id: str, root: str) -> Path:
    _load_state()
    p = Path(root).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"path not found: {p}")
    if not p.is_dir():
        raise FileNotFoundError(f"not a directory: {p}")
    _ROOTS[str(user_id)] = p
    _save_state()
    return p


def clear_root(user_id: str) -> None:
    _load_state()
    _ROOTS.pop(str(user_id), None)
    _save_state()


def resolve_under_root(root: Path, rel: str) -> Path:
    """解析相对路径，禁止逃逸出 root。"""
    root = root.resolve()
    rel = (rel or "").replace("\\", "/").lstrip("/")
    if not rel or rel in (".", "./"):
        return root
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError as e:
        raise PermissionError(f"path escapes workspace root: {rel}") from e
    return target


def build_tree(
    target: Path,
    root: Path,
    *,
    max_depth: int = 2,
    _depth: int = 0,
) -> list[dict[str, Any]]:
    """浅目录树，供 FE 文件浏览器。"""
    if _depth >= max_depth:
        return []
    out: list[dict[str, Any]] = []
    try:
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        return []
    skip = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", ".next"}
    for ent in entries[:200]:
        if ent.name.startswith(".") and ent.name not in (".env.example",):
            if ent.name in skip or ent.name.startswith("."):
                if ent.name in skip:
                    continue
                # hide most dotfiles
                if ent.name not in (".env.example",):
                    continue
        if ent.name in skip:
            continue
        try:
            rel = str(ent.relative_to(root)).replace("\\", "/")
        except ValueError:
            continue
        node: dict[str, Any] = {
            "name": ent.name,
            "path": rel,
            "type": "dir" if ent.is_dir() else "file",
        }
        if ent.is_file():
            try:
                node["size"] = ent.stat().st_size
            except OSError:
                node["size"] = None
        if ent.is_dir() and _depth + 1 < max_depth:
            node["children"] = build_tree(ent, root, max_depth=max_depth, _depth=_depth + 1)
        out.append(node)
    return out


async def exec_command(
    root: Path, command: str, *, timeout: float = 120.0
) -> dict[str, Any]:
    """在 workspace root 下执行命令（优先 list/exec，避免 shell 注入）。"""
    command = (command or "").strip()
    if not command:
        return {"ok": False, "stdout": "", "stderr": "empty command", "code": -1}
    try:
        from backend.core.safe_subprocess import run_capture

        r = await run_capture(
            command,
            cwd=str(root),
            timeout=max(1.0, float(timeout)),
            max_output=200_000,
        )
        return {
            "ok": bool(r.get("ok")),
            "stdout": (r.get("stdout") or "")[-200_000:],
            "stderr": (r.get("stderr") or "")[-50_000:],
            "code": int(r.get("code") if r.get("code") is not None else -1),
            "cwd": str(root),
            "mode": r.get("mode"),
        }
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e), "code": -1}
