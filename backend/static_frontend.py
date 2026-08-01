"""
Mount Next.js static export (if present) so one uvicorn process serves API + UI.

Static candidates (first hit wins among *valid* trees):
  - TAKTON_FRONTEND_STATIC  env override
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


def resolve_frontend_static() -> Path | None:
    env = (os.environ.get("TAKTON_FRONTEND_STATIC") or "").strip()
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env).expanduser().resolve())

    here = Path(__file__).resolve().parent  # backend/
    # 优先 next export / dist，再落 legacy backend/static（避免 07-27 旧包盖住新导出）
    candidates.extend(
        [
            here.parent / "frontend" / "out",
            here.parent / "frontend" / "dist",
            here / "static",
        ]
    )

    valid: list[tuple[int, float, Path]] = []
    for c in candidates:
        try:
            if not c.is_dir() or not (c / "index.html").is_file():
                continue
            cov = _route_coverage(c)
            try:
                mtime = (c / "index.html").stat().st_mtime
            except OSError:
                mtime = 0.0
            valid.append((cov, mtime, c))
        except OSError:
            continue

    if not valid:
        return None
    # 路由覆盖优先，其次 mtime
    valid.sort(key=lambda t: (t[0], t[1]), reverse=True)
    chosen = valid[0][2]
    if len(valid) > 1 and valid[0][0] < 5:
        logger.warning(
            "Frontend static at %s has low route coverage (%s). "
            "Re-export with NEXT_EXPORT=1 to restore agents/kernel/… pages.",
            chosen,
            valid[0][0],
        )
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
