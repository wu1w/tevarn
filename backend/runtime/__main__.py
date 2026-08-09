"""Kernel-first 宿主入口。

用法（仓库根，PYTHONPATH=.）::

    python -m backend.runtime              # HTTP Adapter :8090（默认）
    python -m backend.runtime --host 127.0.0.1 --port 8090
    python -m backend.runtime --headless   # 仅 Runtime（无 uvicorn，dispatcher 循环）

环境：
    TEVARN_AIOS_PROFILE=aios-dev
    TEVARN_SINGLE_USER_MODE=true
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path


def _ensure_path() -> None:
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m backend.runtime",
        description="Tevarn Kernel Host — UI 非必需",
    )
    p.add_argument(
        "--headless",
        action="store_true",
        help="不启 HTTP，仅跑 Dispatcher/后台循环（调试用）",
    )
    p.add_argument("--host", default=os.environ.get("TEVARN_APP_HOST", "127.0.0.1"))
    p.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("TEVARN_APP_PORT", "8090")),
    )
    p.add_argument("--reload", action="store_true", help="uvicorn reload（仅非 headless）")
    return p


def run_http(host: str, port: int, *, reload: bool = False) -> None:
    """经 FastAPI Adapter 暴露 Kernel（生产/桌面默认路径）。"""
    os.environ.setdefault("TEVARN_SINGLE_USER_MODE", "true")
    import uvicorn

    print(f"[tevarn-runtime] Kernel Host + HTTP Adapter http://{host}:{port}")
    print("[tevarn-runtime] UI 可后连；关控制台窗口不应依赖本进程退出（见 Electron 退出语义）")
    uvicorn.run(
        "backend.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


async def _headless_loop() -> None:
    """无 HTTP：初始化 DB + workforce dispatcher 常驻。"""
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger("tevarn.runtime")
    from backend.core.config import settings
    from backend.database import AsyncSessionLocal, init_db
    from backend.kernel import get_kernel
    from backend.kernel.identity import IdentityRegistry
    from backend.kernel.workforce import init_workforce

    await init_db()
    kernel = get_kernel()
    if getattr(kernel, "identity_registry", None) is None:
        kernel.identity_registry = IdentityRegistry(kernel, AsyncSessionLocal)
    inbox, dispatcher = init_workforce(kernel, AsyncSessionLocal, settings)
    log.info(
        "headless runtime up inbox=%s dispatcher=%s",
        inbox is not None,
        dispatcher is not None,
    )
    if dispatcher is None:
        log.error("dispatcher 未启用；检查 agent_dispatcher_enabled / aios profile")
        return
    await dispatcher.run_forever()


def run_headless() -> None:
    print("[tevarn-runtime] headless mode — Ctrl+C 停止")
    try:
        asyncio.run(_headless_loop())
    except KeyboardInterrupt:
        print("\n[tevarn-runtime] stopped")


def main(argv: list[str] | None = None) -> None:
    _ensure_path()
    os.environ.setdefault("TEVARN_AIOS_PROFILE", os.environ.get("TEVARN_AIOS_PROFILE", "aios-dev"))
    args = build_parser().parse_args(argv)
    if args.headless:
        run_headless()
    else:
        run_http(args.host, args.port, reload=args.reload)


if __name__ == "__main__":
    main()
