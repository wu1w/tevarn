"""File tree browser API - sandboxed file browser with mode support

Modes:
  - sandbox (default): constrained to allowed workspace roots
    (file_browser_root + agent workspace + Electron userData/workspace)
  - local: full server filesystem (requires FILE_BROWSER_LOCAL=1)
  - ssh: remote machine via SSH (future)
"""
import logging
import os
import sys
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import get_current_user
from backend.core.config import settings
from backend.schemas.user import UserRead

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/files", tags=["Files"])

def _env_truthy(name: str, default: str = "") -> bool:
    """True only for 1/true/yes/on (case-insensitive). '0'/'false'/'off' → False."""
    v = (os.environ.get(name, default) or "").strip().lower()
    if not v:
        return False
    if v in ("0", "false", "no", "off", "disabled", "disable"):
        return False
    if v in ("1", "true", "yes", "on", "enabled", "enable"):
        return True
    # unknown non-empty: treat as disabled (safe default)
    return False


LOCAL_ENABLED = _env_truthy("FILE_BROWSER_LOCAL")
LOCAL_ROOT = os.path.abspath("/")


def _sandbox_root() -> Path:
    """解析可写沙箱根目录（跨平台）。"""
    raw = (settings.file_browser_root or "workspace").strip() or "workspace"
    root = Path(raw).expanduser()
    if not root.is_absolute():
        # Prefer project root over cwd (cwd may be frontend/ when mis-started)
        try:
            from backend.tools.permissions import detect_project_root

            root = Path(detect_project_root()) / root
        except Exception:
            root = Path.cwd() / root
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _electron_userdata_workspace_candidates() -> list[Path]:
    """Desktop agent often writes under userData/workspace, not project root."""
    home = Path.home()
    cands: list[Path] = []
    if sys.platform == "win32":
        roaming = os.environ.get("APPDATA") or str(home / "AppData" / "Roaming")
        cands.append(Path(roaming) / "tevarn" / "data" / "workspace")
        cands.append(Path(roaming) / "tevarn" / "workspace")
    elif sys.platform == "darwin":
        cands.append(
            home / "Library" / "Application Support" / "tevarn" / "data" / "workspace"
        )
    cands.append(home / ".tevarn" / "data" / "workspace")
    cands.append(home / ".tevarn" / "workspace")
    return cands


def _allowed_sandbox_roots() -> list[Path]:
    """Union of roots the browser may read (deduped, existing dirs preferred)."""
    roots: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        try:
            r = p.expanduser().resolve()
        except Exception:
            return
        key = str(r).lower()
        if key in seen:
            return
        seen.add(key)
        roots.append(r)

    _add(_sandbox_root())
    try:
        from backend.tools.permissions import resolve_agent_workspace_root

        _add(Path(resolve_agent_workspace_root()))
    except Exception:
        pass
    for c in _electron_userdata_workspace_candidates():
        if c.is_dir():
            _add(c)
    return roots


def _normalize_rel(raw_path: str) -> str:
    rel = (raw_path or "").strip().replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    rel = rel.lstrip("/")
    if rel.lower().startswith("workspace/"):
        rel = rel[len("workspace/") :]
    if rel.lower().startswith("sandbox:"):
        rel = rel.split(":", 1)[-1].lstrip("/")
    return rel


def _is_windows_abs(s: str) -> bool:
    s = (s or "").strip()
    if not s:
        return False
    # C:\... or \\server\share
    if len(s) >= 3 and s[1] == ":" and s[2] in ("\\", "/"):
        return True
    if s.startswith("\\\\"):
        return True
    return Path(s).is_absolute()


def _resolve_path(mode: str, raw_path: str) -> tuple[Path, Path]:
    """根据 mode 解析和校验路径，返回 (target_path, base_path)。

    Absolute paths under any allowed sandbox root are accepted (preview/download
    often pass Electron userData abs paths while API root is project ``.``).
    """
    if mode == "local":
        if not LOCAL_ENABLED:
            raise HTTPException(
                status_code=403,
                detail="Local mode is disabled. Set FILE_BROWSER_LOCAL=1 to enable.",
            )
        base = Path(LOCAL_ROOT)
        if not raw_path:
            return base, base
        if _is_windows_abs(raw_path) or Path(raw_path).is_absolute():
            return Path(raw_path).expanduser().resolve(), base
        return (base / raw_path).resolve(), base

    if mode != "sandbox":
        raise HTTPException(status_code=400, detail=f"Unknown mode: {mode}")

    roots = _allowed_sandbox_roots()
    primary = roots[0] if roots else _sandbox_root()
    raw = (raw_path or "").strip()
    if not raw:
        return primary, primary

    # Absolute path → must sit under some allowed root
    if _is_windows_abs(raw) or Path(raw).is_absolute():
        target = Path(raw).expanduser().resolve()
        for base in roots:
            try:
                target.relative_to(base)
                return target, base
            except ValueError:
                continue
        raise HTTPException(
            status_code=403,
            detail="Access denied: path outside allowed workspace roots",
        )

    rel = _normalize_rel(raw)
    # Prefer a root that already contains the file (agent may write to userData)
    for base in roots:
        candidate = (base / rel).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            continue
        if candidate.exists():
            return candidate, base
    # Default: primary root (may 404 later)
    target = (primary / rel).resolve()
    return target, primary


def _check_access(target: Path, base: Path):
    """安全校验：target 必须在 base 或任一 allowed root 之下"""
    try:
        target.relative_to(base)
        return
    except ValueError:
        pass
    for root in _allowed_sandbox_roots():
        try:
            target.relative_to(root)
            return
        except ValueError:
            continue
    raise HTTPException(status_code=403, detail="Access denied") from None


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

    # P2: hard cap to protect memory / UI (2 MiB)
    _MAX_READ = 2 * 1024 * 1024
    try:
        size = target.stat().st_size
    except OSError:
        size = 0
    if size > _MAX_READ:
        raise HTTPException(
            status_code=413,
            detail=f"File too large to read inline ({size} bytes > {_MAX_READ}). Use download.",
        )

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
        raise HTTPException(status_code=500, detail=f"Failed to read file: {e}") from e


@router.get("/download")
async def download_file(
    path: str = Query(..., description="File path relative to mode root"),
    mode: str = Query("sandbox", description="sandbox | local"),
    current_user: Annotated[UserRead, Depends(get_current_user)] = None,
):
    """下载文件（AI 生成的文件在对话栏可一键下载）。

    限制在沙箱内，带 Content-Disposition: attachment 触发浏览器下载。
    """
    from fastapi.responses import FileResponse

    target, base = _resolve_path(mode, path)
    _check_access(target, base)

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{target.name}"'},
    )


@router.get("/resolve")
async def resolve_file_path(
    path: str = Query(..., description="File path relative to mode root"),
    mode: str = Query("sandbox", description="sandbox | local"),
    current_user: Annotated[UserRead, Depends(get_current_user)] = None,
):
    """解析工作区相对路径 → 绝对路径（Electron openPath / 本机打开用）。"""

    rel = (path or "").strip().lstrip("/").replace("\\", "/")
    if rel.startswith("workspace/"):
        rel = rel[len("workspace/") :]
    target, base = _resolve_path(mode, rel)
    _check_access(target, base)
    exists = target.exists() and target.is_file()
    size = 0
    if exists:
        try:
            size = target.stat().st_size
        except OSError:
            size = 0
    return {
        "path": rel,
        "name": target.name,
        "abs_path": str(target.resolve()) if exists or target.parent.exists() else str(target),
        "exists": exists,
        "size": size,
    }


@router.post("/open")
async def open_workspace_file(
    path: str = Query(..., description="File path relative to mode root"),
    mode: str = Query("sandbox", description="sandbox | local"),
    current_user: Annotated[UserRead, Depends(get_current_user)] = None,
):
    """用本机默认应用打开工作区文件（Web 后端同机；Electron 优先用 openPath）。"""
    import subprocess
    import sys

    rel = (path or "").strip().lstrip("/").replace("\\", "/")
    if rel.startswith("workspace/"):
        rel = rel[len("workspace/") :]
    target, base = _resolve_path(mode, rel)
    _check_access(target, base)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    abs_path = str(target.resolve())
    try:
        if os.name == "nt":
            os.startfile(abs_path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(
                ["open", abs_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                ["xdg-open", abs_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception as e:
        logger.warning("open workspace file failed path=%s err=%s", abs_path, e)
        raise HTTPException(status_code=500, detail=f"Failed to open file: {e}") from e
    return {"ok": True, "abs_path": abs_path, "path": rel}


@router.get("/visio-preview")
async def visio_preview(
    path: str = Query(..., description="Visio file path relative to mode root"),
    mode: str = Query("sandbox", description="sandbox | local"),
    as_format: str = Query("json", alias="as", description="json | pdf"),
    current_user: Annotated[UserRead, Depends(get_current_user)] = None,
):
    """Preview Microsoft Visio drawings (.vsd / .vsdx / .vsdm / .vdx).

    VSDX is rendered to SVG in-process. Binary .vsd uses LibreOffice or
    libvisio when installed; otherwise returns extracted strings + install hint.
    """
    from backend.services.visio_preview import is_visio_path, preview_visio

    rel = (path or "").strip().lstrip("/").replace("\\", "/")
    if rel.lower().startswith("workspace/"):
        rel = rel[len("workspace/") :]
    target, base = _resolve_path(mode, rel)
    _check_access(target, base)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if not is_visio_path(target):
        raise HTTPException(status_code=400, detail="Not a Visio file (.vsd/.vsdx/.vdx)")

    try:
        payload = preview_visio(target)
    except Exception as e:
        logger.warning("visio preview failed path=%s err=%s", target, e)
        raise HTTPException(status_code=500, detail=f"Visio preview failed: {e}") from e

    want_pdf = (as_format or "json").lower() == "pdf"
    pdf_path = payload.get("pdf_path")
    if want_pdf:
        from fastapi.responses import FileResponse

        if not pdf_path or not Path(pdf_path).is_file():
            raise HTTPException(
                status_code=422,
                detail=payload.get("hint") or "PDF conversion unavailable for this Visio file",
            )
        return FileResponse(
            path=str(pdf_path),
            filename=f"{target.stem}.pdf",
            media_type="application/pdf",
        )

    payload["path"] = rel
    payload.pop("pdf_path", None)
    return payload


@router.get("/info")
async def get_file_info(
    current_user: Annotated[UserRead, Depends(get_current_user)] = None,
):
    """Get file browser config info"""
    sandbox = _sandbox_root()
    return {
        "sandbox_root": str(sandbox),
        "allowed_roots": [str(r) for r in _allowed_sandbox_roots()],
        "local_enabled": LOCAL_ENABLED,
        "modes": ["sandbox"] + (["local"] if LOCAL_ENABLED else []),
    }


# ── Agent 记忆 / 约定 Markdown（侧栏展示）──────────────────────────

# 固定清单 + 自动扫描 memory/ 日期短记忆
_AGENT_MD_SPECS: list[dict] = [
    {
        "key": "agents",
        "label": "AGENTS.md",
        "names": ["AGENTS.md", "agents.md"],
        "desc": "Agent 行为与工程约定",
        "group": "core",
    },
    {
        "key": "memory",
        "label": "memory.md",
        "names": ["memory.md", "MEMORY.md"],
        "desc": "长期记忆索引（会话启动加载）",
        "group": "memory",
    },
    {
        "key": "memory_temp",
        "label": "memory_temp.md",
        "names": ["memory_temp.md", "MEMORY_TEMP.md"],
        "desc": "临时草稿 / 进行中笔记",
        "group": "memory",
    },
    {
        "key": "soul",
        "label": "SOUL.md",
        "names": ["SOUL.md", "soul.md"],
        "desc": "人格与语气",
        "group": "core",
    },
    {
        "key": "identity",
        "label": "IDENTITY.md",
        "names": ["IDENTITY.md", "identity.md"],
        "desc": "身份与角色",
        "group": "core",
    },
    {
        "key": "claude",
        "label": "CLAUDE.md",
        "names": ["CLAUDE.md", "claude.md"],
        "desc": "项目规则（兼容 Claude Code）",
        "group": "core",
    },
]


def _find_named(root: Path, names: list[str]) -> Path | None:
    for name in names:
        p = root / name
        if p.is_file():
            return p
        p2 = root / "memory" / name
        if p2.is_file():
            return p2
        p3 = root / ".tevarn" / name
        if p3.is_file():
            return p3
    return None


@router.get("/agent-md")
async def list_agent_md_files(
    current_user: Annotated[UserRead, Depends(get_current_user)] = None,
):
    """列出工作区 Agent 相关 Markdown（AGENTS / memory / 日期短记忆等）。"""
    root = _sandbox_root()
    items: list[dict] = []

    for spec in _AGENT_MD_SPECS:
        found = _find_named(root, spec["names"])
        preferred = spec["names"][0]
        if found is not None:
            try:
                rel = str(found.relative_to(root)).replace("\\", "/")
            except ValueError:
                rel = found.name
            size = found.stat().st_size
            exists = True
        else:
            rel = preferred
            size = 0
            exists = False
        items.append(
            {
                "key": spec["key"],
                "label": spec["label"],
                "path": rel,
                "abs_path": str(found.resolve()) if found is not None else str((root / preferred).resolve()),
                "exists": exists,
                "size": size,
                "desc": spec["desc"],
                "group": spec["group"],
            }
        )

    # 日期短记忆 memory/YYYY-MM-DD.md 与扁平 memory-YYYY-MM-DD.md
    dated: list[dict] = []
    mem_dir = root / "memory"
    if mem_dir.is_dir():
        for p in sorted(mem_dir.iterdir(), reverse=True):
            if p.is_file() and p.suffix.lower() == ".md":
                stem = p.stem
                if len(stem) == 10 and stem[4] == "-" and stem[7] == "-":
                    try:
                        rel = str(p.relative_to(root)).replace("\\", "/")
                    except ValueError:
                        rel = f"memory/{p.name}"
                    dated.append(
                        {
                            "key": f"daily_{stem}",
                            "label": p.name,
                            "path": rel,
                            "abs_path": str(p.resolve()),
                            "exists": True,
                            "size": p.stat().st_size,
                            "desc": f"短记忆 · {stem}",
                            "group": "daily",
                        }
                    )
    try:
        for p in root.iterdir():
            if not p.is_file():
                continue
            name = p.name
            if name.startswith("memory-") and name.endswith(".md") and len(name) >= 18:
                dated.append(
                    {
                        "key": f"daily_flat_{name}",
                        "label": name,
                        "path": name,
                        "abs_path": str(p.resolve()),
                        "exists": True,
                        "size": p.stat().st_size,
                        "desc": "短记忆（扁平）",
                        "group": "daily",
                    }
                )
    except OSError:
        pass

    # de-dupe by path
    seen_paths = {i["path"] for i in items}
    for d in dated:
        if d["path"] not in seen_paths:
            items.append(d)
            seen_paths.add(d["path"])

    return {
        "root": str(root),
        "items": items,
    }


@router.post("/agent-md/open")
async def open_agent_md_file(
    path: str = Query(..., description="Relative path under sandbox, e.g. memory.md"),
    current_user: Annotated[UserRead, Depends(get_current_user)] = None,
):
    """用系统默认应用打开沙箱内的 agent md（本机编辑）。

    路径相对 file_browser_root 解析，禁止 .. 与绝对路径穿越。
    Windows: os.startfile；macOS: open；Linux: xdg-open。
    """
    import subprocess
    import sys

    raw = (path or "").strip().replace("\\", "/").lstrip("/")
    if not raw or ".." in raw.split("/") or not raw.lower().endswith(".md"):
        raise HTTPException(status_code=400, detail="Only .md paths under workspace allowed")

    target, base = _resolve_path("sandbox", raw)
    _check_access(target, base)
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {raw}")

    abs_path = str(target.resolve())
    try:
        if sys.platform == "win32":
            os.startfile(abs_path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", abs_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", abs_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        logger.warning("open agent-md failed path=%s err=%s", abs_path, e)
        raise HTTPException(status_code=500, detail=f"Failed to open file: {e}") from e

    return {
        "ok": True,
        "path": raw,
        "abs_path": abs_path,
    }


@router.post("/agent-md/ensure")
async def ensure_agent_md_file(
    path: str = Query(..., description="Relative path under sandbox, e.g. memory.md"),
    current_user: Annotated[UserRead, Depends(get_current_user)] = None,
):
    """若不存在则创建空的 agent md 文件（带简短标题模板）。"""
    # only allow .md under root
    raw = (path or "").strip().replace("\\", "/").lstrip("/")
    if ".." in raw.split("/") or not raw.lower().endswith(".md"):
        raise HTTPException(status_code=400, detail="Only .md paths under workspace allowed")
    target, base = _resolve_path("sandbox", raw)
    _check_access(target, base)
    target.parent.mkdir(parents=True, exist_ok=True)
    created = False
    if not target.exists():
        title = target.stem
        target.write_text(f"# {title}\n\n", encoding="utf-8")
        created = True
    return {
        "path": raw,
        "created": created,
        "exists": True,
        "size": target.stat().st_size,
    }


@router.post("/checkpoint/restore")
async def restore_file_checkpoint(
    body: dict,
    current_user: Annotated[UserRead, Depends(get_current_user)] = None,
):
    """Unified restore: Python registry id / path, or Rust kernel id.

    Preferred body (explicit — no guessing):
      { "backend": "python"|"rust", "checkpoint_id": "...", "path": "..." }

    Compatibility:
      - path starting with rust: → rust
      - 32-char hex opaque id → try **python registry first**, then rust
      - filesystem path under checkpoints/ → python
    """
    body = body or {}
    raw = str(body.get("path") or body.get("snapshot") or "").strip()
    cp_id = str(body.get("checkpoint_id") or "").strip()
    backend = str(body.get("backend") or "").strip().lower()
    session_id_raw = str(body.get("session_id") or "").strip()
    # Never trust client absolute workspace paths (shared-deploy safety).
    # Only session_id → server-side config may bind workspace.
    root_for_restore = ""
    if session_id_raw and current_user is not None:
        try:
            import uuid as _uuid

            from backend.repositories.session_repo import AsyncSessionRepository

            srepo = AsyncSessionRepository()
            sid = _uuid.UUID(session_id_raw)
            sess = await srepo.get_by_id(sid)
            if sess is not None:
                owner = getattr(sess, "user_id", None)
                if owner is not None and str(owner) != str(
                    getattr(current_user, "id", "")
                ):
                    raise HTTPException(
                        status_code=403, detail="session not owned by user"
                    )
                cfg = getattr(sess, "config", None) or {}
                if isinstance(cfg, dict):
                    root_for_restore = str(
                        cfg.get("workspace_root")
                        or cfg.get("file_browser_root")
                        or cfg.get("cwd")
                        or ""
                    ).strip()
        except HTTPException:
            raise
        except Exception as _se:
            logger.debug("restore session workspace resolve skip: %s", _se)

    if raw.startswith("rust:"):
        backend = backend or "rust"
        cp_id = raw[5:].strip() or cp_id
        raw = ""

    # Opaque id in path field
    if not cp_id and raw and "/" not in raw and chr(92) not in raw and "checkpoints" not in raw:
        cp_id = raw
        raw = ""

    # Shared-deploy safety: opaque restore requires session_id (binds workspace + ownership)
    _opaque = bool(cp_id) or (
        bool(raw) and "/" not in raw and chr(92) not in raw and "checkpoints" not in raw
    )
    if _opaque and not session_id_raw:
        allow_loose = False
        try:
            from backend.core.config import settings
            # single-user / local desktop may allow without session
            allow_loose = bool(getattr(settings, "file_browser_local", False)) or bool(
                getattr(settings, "single_user_mode", False)
            )
        except Exception:
            allow_loose = False
        if not allow_loose:
            raise HTTPException(
                status_code=400,
                detail="session_id required for checkpoint restore",
            )

    errors: list[str] = []

    def _try_python(key: str) -> dict | None:
        from contextlib import nullcontext

        from backend.agent.file_checkpoint import restore_checkpoint_file
        from backend.tools.permissions import run_workspace_context

        ctx = (
            run_workspace_context(root_for_restore)
            if root_for_restore
            else nullcontext()
        )
        with ctx:
            result = restore_checkpoint_file(
                key,
                session_id=session_id_raw or None,
                user_id=str(getattr(current_user, "id", "") or "") or None,
            )
        if result.get("ok"):
            result["backend"] = "python"
            if root_for_restore:
                result["workspace_root"] = root_for_restore
            return result
        errors.append(str(result.get("error") or "python restore failed"))
        return None

    async def _try_rust(key: str) -> dict | None:
        try:
            from backend.kernel import get_kernel

            k = get_kernel()
            if not hasattr(k, "_acall"):
                errors.append("Rust kernel host unavailable")
                return None
            result = await k._acall("checkpoint_restore", {"checkpoint_id": key})
            if isinstance(result, dict) and result.get("ok") is False:
                errors.append(str(result.get("error") or result))
                return None
            return {
                "ok": True,
                "backend": "rust",
                "checkpoint_id": key,
                "result": result,
            }
        except Exception as e:
            errors.append(f"rust restore: {e}")
            return None

    # Explicit backend
    if backend == "python":
        key = cp_id or raw
        if not key:
            raise HTTPException(status_code=400, detail="python restore needs checkpoint_id or path")
        hit = _try_python(key)
        if hit:
            return hit
        raise HTTPException(status_code=400, detail="; ".join(errors) or "python restore failed")

    if backend == "rust":
        if not cp_id:
            raise HTTPException(status_code=400, detail="rust restore needs checkpoint_id")
        hit = await _try_rust(cp_id)
        if hit:
            return hit
        raise HTTPException(status_code=400, detail="; ".join(errors) or "rust restore failed")

    # Auto: opaque hex (32) → python registry first, then rust
    if cp_id:
        import re as _re
        if _re.fullmatch(r"[0-9a-fA-F]{32}", cp_id):
            hit = _try_python(cp_id)
            if hit:
                return hit
            hit = await _try_rust(cp_id)
            if hit:
                return hit
        elif cp_id.startswith("rust:"):
            hit = await _try_rust(cp_id[5:])
            if hit:
                return hit
        else:
            # non-hex id: rust first, python fallback
            hit = await _try_rust(cp_id)
            if hit:
                return hit
            hit = _try_python(cp_id)
            if hit:
                return hit

    if raw:
        hit = _try_python(raw)
        if hit:
            return hit

    if not cp_id and not raw:
        raise HTTPException(
            status_code=400,
            detail="path or checkpoint_id required",
        )
    raise HTTPException(
        status_code=400,
        detail="; ".join(errors) or "restore failed",
    )
