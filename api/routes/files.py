"""File tree browser API - sandboxed file browser with mode support

Modes:
  - sandbox (default): constrained to settings.file_browser_root (desktop: userData/workspace)
  - local: full server filesystem (requires FILE_BROWSER_LOCAL=1)
  - ssh: remote machine via SSH (future)
"""
import os
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.core.config import settings
from backend.schemas.user import UserRead
from backend.api.dependencies import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/files", tags=["Files"])

LOCAL_ENABLED = bool(os.environ.get("FILE_BROWSER_LOCAL", "").strip())
LOCAL_ROOT = os.path.abspath("/")


def _sandbox_root() -> Path:
    """解析可写沙箱根目录（跨平台）。"""
    raw = (settings.file_browser_root or "workspace").strip() or "workspace"
    root = Path(raw).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_path(mode: str, raw_path: str) -> tuple[Path, Path]:
    """根据 mode 解析和校验路径，返回 (target_path, base_path)"""
    if mode == "sandbox":
        base = _sandbox_root()
    elif mode == "local":
        if not LOCAL_ENABLED:
            raise HTTPException(
                status_code=403,
                detail="Local mode is disabled. Set FILE_BROWSER_LOCAL=1 to enable.",
            )
        base = Path(LOCAL_ROOT)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown mode: {mode}")

    target = (base / raw_path).resolve() if raw_path else base
    return target, base


def _check_access(target: Path, base: Path):
    """安全校验：target 必须在 base 之下"""
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")


@router.get("/tree")
async def get_file_tree(
    path: str = Query("", description="Subdirectory path"),
    mode: str = Query("sandbox", description="sandbox | local"),
    depth: int = Query(1, description="Tree depth (1 = flat, 2 = one level of children, etc.)", ge=1, le=3),
    current_user: Annotated[UserRead, Depends(get_current_user)] = None,
):
    """Get directory listing (lazy-load: only load `depth` levels deep)"""
    target, base = _resolve_path(mode, path)
    _check_access(target, base)

    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    return _build_tree(target, base, max_depth=depth)


def _build_tree(dir_path: Path, base_path: Path, max_depth: int = 1, current_depth: int = 0) -> list:
    """Lazy-loaded directory tree"""
    items = []
    if current_depth > max_depth:
        return items

    try:
        entries = sorted(dir_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    except PermissionError:
        return items

    for entry in entries:
        # Skip hidden files/dirs and build artifacts
        if entry.name.startswith(".") or entry.name in (
            "node_modules", "__pycache__", "venv", ".venv", "dist", ".next",
        ):
            continue
        if entry.name == "package-lock.json":
            continue

        is_dir = entry.is_dir()
        try:
            rel_path = str(entry.relative_to(base_path))
        except ValueError:
            continue

        item = {
            "name": entry.name,
            "path": rel_path,
            "type": "directory" if is_dir else "file",
        }

        # 只在第一层递归子目录（depth>=2 时），避免一次性展开巨量数据
        if is_dir and current_depth < max_depth:
            item["children"] = _build_tree(entry, base_path, max_depth, current_depth + 1)

        if not is_dir:
            try:
                item["size"] = entry.stat().st_size
            except OSError:
                item["size"] = 0

        items.append(item)

    return items


@router.get("/read")
async def read_file(
    path: str = Query(..., description="File path relative to mode root"),
    mode: str = Query("sandbox", description="sandbox | local"),
    current_user: Annotated[UserRead, Depends(get_current_user)] = None,
):
    """Read a file's contents (text files only)"""
    target, base = _resolve_path(mode, path)
    _check_access(target, base)

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    allowed_extensions = {".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
                         ".toml", ".md", ".txt", ".css", ".scss", ".html",
                         ".cfg", ".ini", ".conf", ".sh", ".bash", ".zsh", ".sql",
                         ".svg", ".xml", ".mjs", ".cjs", ".go", ".rs", ".java", ".vue",
                         ".svelte", ".astro", ".php", ".rb", ".c", ".cpp", ".h", ".hpp"}

    if target.suffix not in allowed_extensions and target.name not in (
        ".env", ".gitignore", "Dockerfile", "Makefile", ".gitattributes"
    ):
        raise HTTPException(status_code=403, detail="File type not allowed")

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        return {
            "path": path,
            "name": target.name,
            "content": content,
            "size": target.stat().st_size,
            "language": target.suffix.lstrip(".") or "text",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {e}")


@router.get("/info")
async def get_file_info(
    current_user: Annotated[UserRead, Depends(get_current_user)] = None,
):
    """Get file browser config info"""
    sandbox = _sandbox_root()
    return {
        "sandbox_root": str(sandbox),
        "local_enabled": LOCAL_ENABLED,
        "modes": ["sandbox"] + (["local"] if LOCAL_ENABLED else []),
    }
