"""全局异常处理框架

提供统一的异常层次结构和 FastAPI 异常处理器。
支持：
1. BizException 业务异常基类（含错误码、HTTP 状态码、用户友好消息）
2. 自动捕获未处理异常并返回结构化 JSON
3. 审计日志记录（生产环境可配置）
"""

import logging
import traceback
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

def _sanitize_for_json(obj: Any) -> Any:
    """递归将 bytes 转为 str，防止 JSON 序列化失败"""
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(item) for item in obj]
    return obj



# ============================================================
# 异常层次结构
# ============================================================

class BizException(Exception):
    """业务异常基类

    Attributes:
        code: 业务错误码（如 "RATE_LIMIT_EXCEEDED"）
        message: 用户友好消息
        http_status: HTTP 状态码
        details: 额外调试信息（生产环境可选）
    """

    def __init__(
        self,
        code: str,
        message: str,
        http_status: int = 400,
        details: Any = None,
    ):
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details
        super().__init__(self.message)


class NotFoundError(BizException):
    """资源不存在"""
    def __init__(self, resource: str = "Resource", identifier: str = ""):
        msg = f"{resource} not found" + (f": {identifier}" if identifier else "")
        super().__init__(code="NOT_FOUND", message=msg, http_status=404)


class PermissionDeniedError(BizException):
    """权限不足"""
    def __init__(self, message: str = "Permission denied"):
        super().__init__(code="PERMISSION_DENIED", message=message, http_status=403)


class UnauthorizedError(BizException):
    """未认证"""
    def __init__(self, message: str = "Unauthorized"):
        super().__init__(code="UNAUTHORIZED", message=message, http_status=401)


class ValidationError(BizException):
    """参数校验失败"""
    def __init__(self, message: str = "Validation failed", details: Any = None):
        super().__init__(code="VALIDATION_ERROR", message=message, http_status=422, details=details)


class RateLimitError(BizException):
    """速率限制"""
    def __init__(self, message: str = "Too many requests"):
        super().__init__(code="RATE_LIMIT_EXCEEDED", message=message, http_status=429)


class ConflictError(BizException):
    """资源冲突"""
    def __init__(self, message: str = "Conflict"):
        super().__init__(code="CONFLICT", message=message, http_status=409)


class ExternalServiceError(BizException):
    """外部服务调用失败"""
    def __init__(self, service: str, message: str = "External service error", details: Any = None):
        super().__init__(
            code=f"{service.upper()}_ERROR",
            message=message,
            http_status=502,
            details=details,
        )


# ============================================================
# 异常处理器
# ============================================================

def _make_error_response(
    code: str,
    message: str,
    http_status: int,
    details: Any = None,
    request_id: str | None = None,
) -> JSONResponse:
    """构造统一错误响应

    同时提供 error.message 与 detail 字段，兼容 axios/FastAPI 常见客户端。
    """
    body: dict[str, Any] = {
        "detail": message,  # FastAPI/axios 惯例
        "error": {
            "code": code,
            "message": message,
        },
    }
    if details is not None:
        body["error"]["details"] = _sanitize_for_json(details)
    if request_id:
        body["error"]["request_id"] = request_id
    return JSONResponse(status_code=http_status, content=_sanitize_for_json(body))


async def biz_exception_handler(request: Request, exc: BizException) -> JSONResponse:
    """处理 BizException"""
    request_id = getattr(request.state, "request_id", None)
    logger.warning(
        "BizException: %s - %s",
        exc.code,
        exc.message,
        extra={"request_id": request_id, "error_code": exc.code},
    )
    return _make_error_response(
        code=exc.code,
        message=exc.message,
        http_status=exc.http_status,
        details=exc.details,
        request_id=request_id,
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """处理 Starlette HTTPException（含 FastAPI 的 HTTPException）"""
    request_id = getattr(request.state, "request_id", None)
    logger.warning(
        "HTTPException: %d - %s",
        exc.status_code,
        exc.detail,
        extra={"request_id": request_id},
    )
    return _make_error_response(
        code="HTTP_ERROR",
        message=str(exc.detail),
        http_status=exc.status_code,
        request_id=request_id,
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """处理 Pydantic 参数校验错误"""
    request_id = getattr(request.state, "request_id", None)
    errors = exc.errors()
    logger.warning(
        "ValidationError: %s",
        errors,
        extra={"request_id": request_id},
    )
    return _make_error_response(
        code="VALIDATION_ERROR",
        message="Request validation failed",
        http_status=422,
        details=errors,
        request_id=request_id,
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底：处理所有未捕获的异常"""
    request_id = getattr(request.state, "request_id", None)
    logger.error(
        "Unhandled exception: %s",
        exc,
        exc_info=True,
        extra={"request_id": request_id},
    )
    return _make_error_response(
        code="INTERNAL_ERROR",
        message="An unexpected error occurred",
        http_status=500,
        request_id=request_id,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """注册所有异常处理器到 FastAPI 应用"""
    app.add_exception_handler(BizException, biz_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)  # type: ignore[arg-type]
    logger.info("Exception handlers registered")