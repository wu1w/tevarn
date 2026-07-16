"""
Mount Next.js static export (if present) so one uvicorn process serves API + UI.

Static candidates (first hit wins):
  - backend/static          (pip / monorepo build output)
  - ../frontend/dist        (dev export dir, when index.html exists)
  - TAKTON_FRONTEND_STATIC  env override
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)


def resolve_frontend_static() -> Path | None:
    env = (os.environ.get("TAKTON_FRONTEND_STATIC") or "").strip()
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env).expanduser().resolve())

    here = Path(__file__).resolve().parent  # backend/
    candidates.extend(
        [
            here / "static",
            here.parent / "frontend" / "dist",
            here.parent / "frontend" / "out",
        ]
    )

    for c in candidates:
        try:
            if c.is_dir() and (c / "index.html").is_file():
                return c
        except OSError:
            continue
    return None


def mount_frontend_static(app: FastAPI) -> Path | None:
    """Register SPA static hosting. Must be called AFTER API routes."""
    root = resolve_frontend_static()
    if root is None:
        logger.info("Frontend static not found — API-only mode")
        return None

    next_dir = root / "_next"
    if next_dir.is_dir():
        app.mount("/_next", StaticFiles(directory=str(next_dir)), name="next_assets")

    # Common public assets exported next to index.html
    for name in ("favicon.ico", "icon.png", "robots.txt"):
        pass  # served via catch-all FileResponse

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        # Never shadow API / uploads / docs
        blocked = ("api", "uploads", "docs", "redoc", "openapi.json", "health")
        first = full_path.split("/", 1)[0]
        if first in blocked or full_path in blocked:
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        # Exact file
        candidate = (root / full_path).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            return JSONResponse({"detail": "Not Found"}, status_code=404)

        if candidate.is_file():
            return FileResponse(candidate)

        # trailingSlash export: path/index.html
        as_index = root / full_path / "index.html"
        if as_index.is_file():
            return FileResponse(as_index, media_type="text/html")

        # SPA / missing client route → index.html
        index = root / "index.html"
        if index.is_file():
            return FileResponse(index, media_type="text/html")

        return JSONResponse({"detail": "Frontend not built"}, status_code=503)

    logger.info("Frontend static mounted from %s", root)
    return root
