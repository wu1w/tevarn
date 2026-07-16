"""
Build Next.js static export into backend/static for single-process serving.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
DEST = Path(__file__).resolve().parent / "static"

CANDIDATE_DIRS = [
    FRONTEND / "dist",           # distDir + export often lands files here when index.html present
    FRONTEND / "out",
    FRONTEND / "dist" / "export",
]


def _has_index(p: Path) -> bool:
    return p.is_dir() and (p / "index.html").is_file()


def find_export_dir() -> Path | None:
    for c in CANDIDATE_DIRS:
        if _has_index(c):
            return c
    return None


def build_frontend(force: bool = True) -> Path:
    if not FRONTEND.is_dir():
        raise FileNotFoundError(f"frontend directory not found: {FRONTEND}")

    npm = "npm.cmd" if os.name == "nt" else "npm"
    env = {**os.environ, "NEXT_EXPORT": "1"}

    # Ensure deps
    if not (FRONTEND / "node_modules").is_dir():
        print("[build_frontend] npm ci ...")
        subprocess.run([npm, "ci"], cwd=str(FRONTEND), check=True, env=env)

    if force or find_export_dir() is None:
        print("[build_frontend] NEXT_EXPORT=1 npm run build ...")
        subprocess.run([npm, "run", "build"], cwd=str(FRONTEND), check=True, env=env)

    src = find_export_dir()
    if src is None:
        raise RuntimeError(
            "Next export output not found. Expected index.html under "
            "frontend/dist, frontend/out, or frontend/dist/export"
        )

    if DEST.exists():
        shutil.rmtree(DEST)
    # Copy only static-serving files; skip Next server intermediates if mixed
    skip_dirs = {"server", "cache", "diagnostics", "types", "build", "turbopack"}
    DEST.mkdir(parents=True, exist_ok=True)

    for item in src.iterdir():
        if item.name in skip_dirs:
            continue
        # Skip server-only manifests if present alongside export
        if item.name.endswith(".nft.json") or item.name in {
            "required-server-files.json",
            "required-server-files.js",
            "trace",
            "trace-build",
            "BUILD_ID",
            "app-path-routes-manifest.json",
            "prerender-manifest.json",
            "routes-manifest.json",
            "images-manifest.json",
            "export-marker.json",
            "fallback-build-manifest.json",
            "build-manifest.json",
            "package.json",
            "next-minimal-server.js.nft.json",
            "next-server.js.nft.json",
        }:
            # Still allow if export actually needs them — none needed for static host
            if item.suffix in {".html", ".txt", ".ico", ".png", ".svg", ".json"} and item.is_file():
                # keep html/txt etc. handled below
                pass
            if item.is_file() and item.suffix not in {".html", ".txt", ".ico", ".png", ".svg", ".css", ".js", ".map", ".woff", ".woff2"}:
                if item.name not in {"index.html", "404.html"} and not item.name.endswith(".html"):
                    # copy html always; skip heavy server manifests
                    if item.suffix == ".json" and "manifest" in item.name:
                        continue

        target = DEST / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)

    if not (DEST / "index.html").is_file():
        raise RuntimeError(f"Copy failed — no index.html in {DEST}")

    print(f"[build_frontend] static assets ready at {DEST}")
    return DEST


def main() -> None:
    try:
        build_frontend(force=True)
    except Exception as e:
        print(f"[build_frontend] FAILED: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
