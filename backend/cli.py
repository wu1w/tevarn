"""
Takton CLI — 一键启动 / 构建

用法:
  takton start              # 生产：单进程 uvicorn（API + 静态前端）
  takton start --dev        # 开发：仅后端 reload（前端请另开 next dev）
  takton build              # 构建前端到 backend/static
  takton version
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def cmd_build(_: argparse.Namespace) -> int:
    from backend.build_frontend import build_frontend

    build_frontend(force=True)
    return 0


def cmd_version(_: argparse.Namespace) -> int:
    print("takton 0.1.0")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    # Ensure repo root (parent of backend/) is on path when run as script
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    if not args.dev:
        static_index = Path(__file__).resolve().parent / "static" / "index.html"
        if not static_index.is_file():
            # Try to build if frontend source exists
            frontend = root / "frontend"
            if frontend.is_dir() and (frontend / "package.json").is_file():
                print("[takton] 前端未构建，正在构建静态资源…")
                try:
                    from backend.build_frontend import build_frontend

                    build_frontend(force=True)
                except Exception as e:
                    print(
                        f"[takton] 自动构建失败: {e}\n"
                        "请先执行: takton build\n"
                        "或设置 TAKTON_FRONTEND_STATIC 指向已导出的静态目录。",
                        file=sys.stderr,
                    )
                    return 1
            else:
                print(
                    "[takton] 未找到 frontend 静态文件。\n"
                    "  - 开发：另开终端 cd frontend && npm run dev\n"
                    "  - 生产：takton build 后再 start，或 pip 包内应已含 backend/static",
                    file=sys.stderr,
                )
                # still start API-only

    # Desktop-friendly defaults
    os.environ.setdefault("TAKTON_SINGLE_USER_MODE", "true")
    host = args.host
    port = args.port

    import uvicorn

    print(f"[takton] starting http://{host}:{port}  (dev={args.dev})")
    uvicorn.run(
        "backend.main:app",
        host=host,
        port=port,
        reload=args.dev,
        log_level="debug" if args.dev else "info",
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="takton", description="Takton — 个人专属 Agent 终端")
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("start", help="启动服务")
    sp.add_argument("--dev", action="store_true", help="开发模式（uvicorn --reload）")
    sp.add_argument("--host", default=os.environ.get("TAKTON_APP_HOST", "127.0.0.1"))
    sp.add_argument("--port", type=int, default=int(os.environ.get("TAKTON_APP_PORT", "8090")))
    sp.set_defaults(func=cmd_start)

    bp = sub.add_parser("build", help="构建前端静态资源到 backend/static")
    bp.set_defaults(func=cmd_build)

    vp = sub.add_parser("version", help="显示版本")
    vp.set_defaults(func=cmd_version)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        sys.exit(0)
    code = args.func(args)
    if code:
        sys.exit(code)


if __name__ == "__main__":
    main()
