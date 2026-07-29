"""HTTP Adapter 门面。

当前实现仍在 ``backend.api`` / ``backend.main``（历史路径）。
对外请优先::

    from backend.adapters.http import get_fastapi_app  # 懒加载

便于日后把 app 工厂迁到本包而不改调用方。
"""

from __future__ import annotations

from typing import Any


def get_fastapi_app() -> Any:
    """返回 FastAPI app 单例（``backend.main:app``）。"""
    from backend.main import app

    return app


def register_api_routes(app: Any, *, prefix: str = "/api") -> None:
    """注册 REST 路由（委托既有 register_routes）。"""
    from backend.api.routes import register_routes

    register_routes(app, prefix=prefix)
