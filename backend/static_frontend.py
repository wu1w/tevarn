"""
Mount Next.js static export (if present) so one uvicorn process serves API + UI.

Static candidates (first hit wins among *valid* trees):
  - TEVARN_FRONTEND_STATIC  env override
  - ../frontend/out         (next export)
  - ../frontend/dist
  - backend/static          (legacy pip / monorepo build output)

P0: 旧 backend/static 缺 agents/kernel 等路由时，若 frontend/out 更新则优先它。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

# 产品路由：导出树必须有对应 index.html，否则显式 404（勿静默回落到聊天页）
_APP_ROUTES = frozenset({
    "agents",
    "kernel",
    "activity",
    "audit",
    "goals",
    "market",
    "approvals",
    "chat",
    "settings",
    "tools",
    "skills",
    "workflows",
    "wiki",
    "memory",
    "knowledge",
    "devices",
    "cron",
    "channels",
    "cluster",
    "security",
    "profiles",
    "login",
    "config",
    "context",
    "evolution",
    "mcp",
})


def _route_coverage(root: Path) -> int:
    """统计导出树中已有的产品路由目录数（用于挑较新构建）。"""
    n = 0
    for name in _APP_ROUTES:
        if (root / name / "index.html").is_file() or (root / f"{name}.html").is_file():
            n += 1
    return n



def _product_version() -> str:
    try:
        from backend.core.version import product_version
        return str(product_version() or "").strip()
    except Exception:
        try:
            return (Path(__file__).resolve().parent / "VERSION").read_text().strip()
        except Exception:
            return ""


def _static_version_info(root: Path) -> dict:
    """Inspect static tree for version.json / alpha markers."""
    prod = _product_version()
    found = ""
    stale = False
    reason = ""
    try:
        vj = root / "version.json"
        if vj.is_file():
            try:
                import json
                data = json.loads(vj.read_text(encoding="utf-8"))
                found = str(data.get("version") or data.get("app_version") or "").strip()
            except Exception:
                pass
        if not found:
            for rel in ("index.html", "manifest.json"):
                p = root / rel
                if not p.is_file():
                    continue
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")[:12000]
                except Exception:
                    continue
                if "0.5.4-alpha" in text:
                    found = "0.5.4-alpha"
                    break
        if found in {"0.5.4-alpha", "0.5.4"}:
            stale = True
            reason = f"legacy_alpha:{found}"
        elif found and prod:
            try:
                # Compare major.minor only (0.4.2 vs 0.5.4-alpha already handled above)
                fmm = ".".join(found.split(".")[:2])
                pmm = ".".join(prod.split(".")[:2])
                if fmm and pmm and fmm != pmm:
                    stale = True
                    reason = f"version_mismatch:{found}!={prod}"
            except Exception:
                if found != prod:
                    stale = True
                    reason = f"version_mismatch:{found}!={prod}"
        # backend/static without version.json under product 0.4.2 → treat as stale
        if not found and root.name == "static" and prod:
            stale = True
            reason = "legacy_static_no_version_json"
    except Exception as e:
        logger.debug("static version inspect skip: %s", e)
    return {"product": prod, "found": found, "stale": stale, "reason": reason}


def is_static_stale(root: Path) -> bool:
    return bool(_static_version_info(root).get("stale"))


def _warn_if_stale_static(root: Path) -> bool:
    """Log if stale; return True when tree should not be served (unless override)."""
    info = _static_version_info(root)
    if not info.get("stale"):
        return False
    logger.warning(
        "frontend static at %s STALE (found=%s product=%s reason=%s). "
        "Rebuild: cd frontend && npm run build:export. "
        "Emergency override: TEVARN_ALLOW_STALE_STATIC=1",
        root,
        info.get("found") or "unknown",
        info.get("product") or "unknown",
        info.get("reason") or "stale",
    )
    return True


def resolve_frontend_static() -> Path | None:
    env = (os.environ.get("TEVARN_FRONTEND_STATIC") or "").strip()
    allow_stale = (os.environ.get("TEVARN_ALLOW_STALE_STATIC") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env).expanduser().resolve())

    here = Path(__file__).resolve().parent  # backend/
    candidates.extend(
        [
            here.parent / "frontend" / "out",
            here.parent / "frontend" / "dist",
            here / "static",
        ]
    )

    valid: list[tuple[int, float, Path, bool]] = []
    for c in candidates:
        try:
            if not c.is_dir() or not (c / "index.html").is_file():
                continue
            cov = _route_coverage(c)
            try:
                mtime = (c / "index.html").stat().st_mtime
            except OSError:
                mtime = 0.0
            stale = is_static_stale(c)
            if stale and not allow_stale:
                _warn_if_stale_static(c)
                logger.error(
                    "skipping stale frontend static %s (set TEVARN_ALLOW_STALE_STATIC=1 to force)",
                    c,
                )
                continue
            if stale and allow_stale:
                _warn_if_stale_static(c)
            valid.append((cov, mtime, c, stale))
        except OSError:
            continue

    if not valid:
        logger.warning(
            "no valid frontend static tree found (all missing or stale). "
            "API-only mode; run: cd frontend && npm run build:export"
        )
        return None
    # Prefer non-stale, then route coverage, then mtime
    valid.sort(key=lambda t: (0 if t[3] else 1, t[0], t[1]), reverse=True)
    chosen = valid[0][2]
    logger.info("frontend static chosen: %s (coverage check done)", chosen)
    return chosen


def mount_frontend_static(app: FastAPI) -> Path | None:
    """Register SPA static hosting. Must be called AFTER API routes."""
    root = resolve_frontend_static()
    if root is None:
        logger.info("Frontend static not found — API-only mode")
        return None

    next_dir = root / "_next"
    if next_dir.is_dir():
        app.mount("/_next", StaticFiles(directory=str(next_dir)), name="next_assets")

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"])
    async def serve_frontend(request: Request, full_path: str):
        # 归一化：//agents → agents；去掉前导 /
        path = (full_path or "").replace("\\", "/")
        while "//" in path:
            path = path.replace("//", "/")
        path = path.lstrip("/")

        # Never shadow API / uploads / docs
        blocked = ("api", "uploads", "docs", "redoc", "openapi.json", "health", "ws")
        first = path.split("/", 1)[0] if path else ""
        if first in blocked or path in blocked:
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        # Exact file
        if path:
            candidate = (root / path).resolve()
            try:
                candidate.relative_to(root.resolve())
            except ValueError:
                return JSONResponse({"detail": "Not Found"}, status_code=404)

            if candidate.is_file():
                if request.method == "HEAD":
                    return Response(
                        status_code=200,
                        media_type=_guess_media(candidate),
                        headers={"Content-Length": str(candidate.stat().st_size)},
                    )
                return FileResponse(candidate)

            # trailingSlash export: path/index.html
            as_index = root / path / "index.html"
            if as_index.is_file():
                if request.method == "HEAD":
                    return Response(status_code=200, media_type="text/html")
                return FileResponse(as_index, media_type="text/html")

            # 产品路由 / 动态段：统一 SPA 回落（客户端路由 + 客户端 404 页兜底）
            # 硬 404 会误杀未重新导出的部署；导出树有对应 index 时上面已命中。
            top = first.lower() if first else ""
            # 单段且不在产品路由表：优先真实 404 页（避免 /nonexistent 静默成工作台）
            if (
                top
                and top not in _APP_ROUTES
                and top not in ("_next", "favicon.ico", "assets", "fonts", "public")
                and "/" not in path.rstrip("/")
            ):
                not_found = root / "404.html"
                if not not_found.is_file():
                    not_found = root / "404" / "index.html"
                if not not_found.is_file():
                    not_found = root / "_not-found" / "index.html"
                if not_found.is_file():
                    if request.method == "HEAD":
                        return Response(status_code=404, media_type="text/html")
                    return FileResponse(
                        not_found, media_type="text/html", status_code=404
                    )

        # SPA fallback：index.html（client-side router 接管）
        index = root / "index.html"
        if index.is_file():
            if request.method == "HEAD":
                return Response(status_code=200, media_type="text/html")
            return FileResponse(index, media_type="text/html")

        return JSONResponse({"detail": "Frontend not built"}, status_code=503)

    logger.info(
        "Frontend static mounted from %s (routes≈%s)",
        root,
        _route_coverage(root),
    )
    return root


def _guess_media(path: Path) -> str:
    suf = path.suffix.lower()
    return {
        ".html": "text/html",
        ".js": "application/javascript",
        ".css": "text/css",
        ".json": "application/json",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
        ".woff": "font/woff",
        ".woff2": "font/woff2",
        ".txt": "text/plain",
    }.get(suf, "application/octet-stream")
