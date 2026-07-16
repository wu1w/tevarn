"""
全局安全响应头中间件
"""

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from backend.core.config import settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """为所有响应添加基础安全头"""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"

        # Next 静态前端 / 桌面壳需要 inline script；生产可再收紧
        # single_user 桌面场景默认放宽，否则会永久卡在「加载中...」
        if getattr(settings, "single_user_mode", False):
            script_src = "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
            style_src = "style-src 'self' 'unsafe-inline'; "
        else:
            script_src = "script-src 'self' 'unsafe-inline'; "
            style_src = "style-src 'self' 'unsafe-inline'; "

        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            f"{script_src}"
            f"{style_src}"
            "img-src 'self' data: blob: https:; "
            "font-src 'self' data:; "
            "connect-src 'self' ws: wss: http: https:; "
            "media-src 'self' blob:; "
            "object-src 'none'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self';"
        )

        # 仅在 HTTPS 请求或显式开启时附加 HSTS
        if request.url.scheme == "https" or request.headers.get("X-Forwarded-Proto") == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )

        return response
