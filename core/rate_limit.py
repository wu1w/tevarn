"""
基于用户ID的分布式滑动窗口速率限制中间件
- 支持用户ID + IP 双重维度
- 可配置每个维度的阈值
- 内存存储，服务重启后计数清零
- 支持豁免路径和豁免用户
"""

import time
import logging
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    速率限制中间件

    基于用户ID（优先）和客户端 IP（fallback）的双维度滑动窗口限流：
    - 默认每分钟最多 60 个请求
    - 认证用户使用 user_id 限流，未认证用户使用 IP 限流
    - 超过限制返回 429 Too Many Requests
    - 内存存储，服务重启后计数清零
    """

    def __init__(
        self,
        app,
        max_requests: int = 60,
        window_seconds: int = 60,
        exempt_paths: set[str] | None = None,
        exempt_user_ids: set[str] | None = None,
    ):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.exempt_paths = exempt_paths or {"/health", "/docs", "/openapi.json", "/api/health"}
        self.exempt_user_ids = exempt_user_ids or set()
        # key -> list of timestamps (key = user_id or ip)
        self._requests: dict[str, list[float]] = {}

    def _get_max_requests(self, request: Request) -> int:
        # 已认证用户给更高配额，避免 SPA 启动时多接口并发触发 429
        if hasattr(request.state, "user") and request.state.user:
            return max(self.max_requests * 5, 300)
        return self.max_requests

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # 跳过 exempt 路径
        if request.url.path in self.exempt_paths:
            return await call_next(request)

        # 跳过 CORS 预检请求
        if request.method == "OPTIONS":
            return await call_next(request)

        # 获取限流 key：优先 user_id，fallback 到 IP
        rate_limit_key = self._get_rate_limit_key(request)

        # 检查是否豁免用户
        if rate_limit_key in self.exempt_user_ids:
            return await call_next(request)

        now = time.time()

        # 清理过期记录
        self._cleanup(rate_limit_key, now)

        # 检查是否超限
        max_requests = self._get_max_requests(request)
        if len(self._requests.get(rate_limit_key, [])) >= max_requests:
            logger.warning(f"Rate limit exceeded for key: {rate_limit_key[:20]}...")
            return Response(
                content='{"detail":"Rate limit exceeded. Please slow down."}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(self.window_seconds)},
            )

        # 记录本次请求
        self._requests.setdefault(rate_limit_key, []).append(now)

        return await call_next(request)

    def _get_rate_limit_key(self, request: Request) -> str:
        """获取限流 key：优先 user_id，fallback 到 IP"""
        # 尝试从请求中获取用户ID（通过已认证的用户）
        if hasattr(request.state, "user") and request.state.user:
            user_id = getattr(request.state.user, "id", None) or getattr(request.state.user, "user_id", None)
            if user_id:
                return f"user:{user_id}"

        # fallback 到 IP
        return f"ip:{self._get_client_ip(request)}"

    def _get_client_ip(self, request: Request) -> str:
        """获取客户端真实 IP（支持 X-Forwarded-For）"""
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
        if request.client and request.client.host:
            return request.client.host
        return "unknown"

    def _cleanup(self, key: str, now: float) -> None:
        """清理过期的请求记录"""
        cutoff = now - self.window_seconds
        if key in self._requests:
            self._requests[key] = [
                ts for ts in self._requests[key] if ts > cutoff
            ]
            if not self._requests[key]:
                del self._requests[key]