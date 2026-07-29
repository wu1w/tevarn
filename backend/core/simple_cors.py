"""CORS 中间件 —— 本地优先场景下的跨源准入。

## 为什么不能反射任意 Origin

此前的实现把请求里的 Origin 原样回写成 Access-Control-Allow-Origin，并配
Allow-Credentials: true。在**本地优先**产品里这恰恰是最危险的组合，因为
`api/dependencies.get_current_user` 对 single_user_mode 的免登录放行是按
「TCP 对端是不是 loopback」判断的 —— 而用户浏览器访问恶意网页时，那个网页发往
127.0.0.1 的请求，对端就是 loopback。于是：

    用户打开 evil.com
      → 页面 JS fetch('http://127.0.0.1:8000/api/settings', {credentials:'include'})
      → 后端看到对端 127.0.0.1，按 single_user_mode 直接当 admin
      → 旧 CORS 反射 evil.com 并允许携带凭证
      → evil.com 读到全部 API Key / 知识库，还能驱动 Agent 跑 shell

「本地优先」是收紧 CORS 的理由，不是放松的理由。

## 现在的规则

1. **没有 Origin 头** → 非浏览器客户端（curl / CLI / takton-code / Electron 主进程
   反代）。放行，不加 CORS 头（它们也不需要）。
2. **Origin 是 loopback**（127.0.0.1 / localhost / ::1，任意端口）→ 放行并回写。
   覆盖 Electron（http://127.0.0.1:<FRONTEND_PORT>）与 next dev（:3000/:3001），
   零配置。
3. **Origin 在 settings.cors_allowed_origins 里** → 放行并回写。给把 Takton 开给
   局域网 / 自建域名的人留的口子。
4. **其余** → 403 拒绝。

第 4 条是拒绝而不是「只是不回写 CORS 头」：简单请求（GET、表单 POST）即便被浏览器
挡住读响应，**请求本身照样发出去并产生副作用**。在免登录放行的前提下那就是 CSRF。
非浏览器客户端不发 Origin，所以这条不会误伤任何正常用法。
"""

from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

_ALLOWED_METHODS = "GET, POST, PUT, DELETE, PATCH, OPTIONS"
_ALLOWED_HEADERS = "Content-Type, Authorization, X-Requested-With, X-API-Key"
# 预检结果缓存 10 分钟；此前是 "0"，等于每个非简单请求都要多付一次往返。
_MAX_AGE = "600"


def _is_loopback_origin(origin: str) -> bool:
    """Origin 的主机是否 loopback。Origin 由浏览器设置，页面 JS 无法伪造。"""
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = (parts.hostname or "").strip()
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _configured_origins() -> frozenset[str]:
    try:
        from backend.core.config import settings

        raw = getattr(settings, "cors_allowed_origins", "") or ""
    except Exception:
        return frozenset()
    if isinstance(raw, (list, tuple, set)):
        items = [str(x) for x in raw]
    else:
        items = str(raw).replace(",", " ").split()
    return frozenset(o.strip().rstrip("/").lower() for o in items if o.strip())


def is_origin_allowed(origin: str) -> bool:
    """跨源准入判定（中间件与测试共用）。"""
    if not origin:
        return True  # 无 Origin = 非浏览器客户端
    normalized = origin.strip().rstrip("/").lower()
    allowed = _configured_origins()
    if "*" in allowed:
        return True  # 用户显式配置的通配，后果自负
    if normalized in allowed:
        return True
    return _is_loopback_origin(normalized)


class SimpleCORSMiddleware(BaseHTTPMiddleware):
    """按 Origin 白名单准入；非浏览器客户端（无 Origin）不受影响。"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        origin = request.headers.get("origin", "")

        if origin and not is_origin_allowed(origin):
            logger.warning(
                "CORS: rejected cross-origin request from %s to %s %s "
                "(set TAKTON_CORS_ALLOWED_ORIGINS to allow it)",
                origin,
                request.method,
                request.url.path,
            )
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        f"Cross-origin request from '{origin}' is not allowed. "
                        "Takton only accepts requests from the local app by default; "
                        "set TAKTON_CORS_ALLOWED_ORIGINS to permit additional origins."
                    )
                },
                headers={"Vary": "Origin"},
            )

        if request.method == "OPTIONS":
            response: Response = Response(content="", status_code=200, media_type="text/plain")
        else:
            response = await call_next(request)

        # 只对通过准入的浏览器请求回写 CORS 头。无 Origin 的客户端不需要，
        # 也不该拿到 "*" —— 那与 Allow-Credentials: true 组合本身就是非法的，
        # 浏览器会直接拒绝。
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Methods"] = _ALLOWED_METHODS
            response.headers["Access-Control-Allow-Headers"] = _ALLOWED_HEADERS
            response.headers["Access-Control-Max-Age"] = _MAX_AGE

        # 无论是否回写都要声明按 Origin 变化，避免中间层缓存串源
        response.headers["Vary"] = "Origin"
        return response
