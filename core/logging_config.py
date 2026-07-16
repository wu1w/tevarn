"""结构化日志系统 + 日志轮转

提供统一的日志配置，支持：
1. 结构化JSON输出（生产环境）
2. 人可读格式（开发环境）
3. 自动日志轮转（按大小/时间）
4. 请求ID追踪
5. 敏感字段脱敏
"""

import json
import logging
import logging.handlers
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import settings

# 敏感字段列表（日志输出时自动脱敏）
SENSITIVE_FIELDS = {"password", "secret", "token", "api_key", "authorization", "cookie", "session_id"}


def _mask_sensitive(data: Any, depth: int = 0) -> Any:
    """递归脱敏，最多3层"""
    if depth > 3:
        return "..."
    if isinstance(data, dict):
        return {
            k: ("****" if k.lower() in SENSITIVE_FIELDS else _mask_sensitive(v, depth + 1))
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_mask_sensitive(v, depth + 1) for v in data]
    return data


# LogRecord 标准字段，勿塞进 extra（旧实现会把整个 record 打进去，且 json.dumps 遇
# 不可序列化对象时静默失败，导致 Unhandled exception 日志丢失）
_LOGRECORD_STANDARD_ATTRS = {
    "name", "msg", "args", "created", "filename", "funcName", "levelname",
    "levelno", "lineno", "module", "msecs", "message", "pathname", "process",
    "processName", "relativeCreated", "thread", "threadName", "exc_info",
    "exc_text", "stack_info", "taskName", "asctime", "request_id", "user_id",
}


class JSONFormatter(logging.Formatter):
    """结构化JSON格式化器"""

    def format(self, record: logging.LogRecord) -> str:
        try:
            log_entry: dict[str, Any] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "module": record.module,
                "function": record.funcName,
                "line": record.lineno,
            }
            request_id = getattr(record, "request_id", None)
            if request_id:
                log_entry["request_id"] = request_id
            user_id = getattr(record, "user_id", None)
            if user_id is not None:
                log_entry["user_id"] = str(user_id)
            if record.exc_info:
                try:
                    log_entry["exception"] = self.formatException(record.exc_info)
                except Exception:
                    log_entry["exception"] = str(record.exc_info)
            extra = {
                k: v
                for k, v in record.__dict__.items()
                if k not in _LOGRECORD_STANDARD_ATTRS and not k.startswith("_")
            }
            if extra:
                log_entry["extra"] = _mask_sensitive(extra)
            return json.dumps(log_entry, ensure_ascii=False, default=str)
        except Exception as fmt_err:
            # 绝不能让 formatter 抛错导致异常日志彻底丢失
            return json.dumps(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "level": "ERROR",
                    "logger": "logging",
                    "message": f"Log formatting failed: {fmt_err}; original={getattr(record, 'msg', '')!s}",
                },
                ensure_ascii=False,
                default=str,
            )


class HumanFormatter(logging.Formatter):
    """人可读日志格式化器（开发环境）"""
    _RESET = "\033[0m"
    _COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[41m",  # Red background
    }

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now().strftime("%H:%M:%S")
        color = self._COLORS.get(record.levelname, self._RESET)
        msg = record.getMessage()
        req_id = getattr(record, "request_id", "")
        prefix = f"[{ts}] {color}{record.levelname:<8}{self._RESET}"
        if req_id:
            prefix += f" [{req_id}]"
        prefix += f" {record.name}:"
        return f"{prefix} {msg}"


class RequestIDAdapter(logging.LoggerAdapter):
    """自动注入 request_id 的 Logger Adapter"""

    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:
        extra = kwargs.get("extra", {})
        if "request_id" not in extra:
            extra["request_id"] = self.extra.get("request_id", "")
        kwargs["extra"] = extra
        return msg, kwargs


def setup_logging(
    log_dir: str | None = None,
    log_level: str | None = None,
    json_output: bool | None = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 30,
) -> None:
    """配置全局日志系统

    Args:
        log_dir: 日志目录，默认 ~/.takton/logs
        log_level: 日志级别，默认从 settings 读取
        json_output: JSON 输出模式，默认生产环境启用
        max_bytes: 单个日志文件最大字节数
        backup_count: 保留的日志文件数
    """
    log_dir = log_dir or os.path.join(str(Path.home()), ".takton", "logs")
    log_level = log_level or getattr(settings, "LOG_LEVEL", "INFO")
    json_output = json_output if json_output is not None else (not sys.stderr.isatty())

    os.makedirs(log_dir, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 清除已有 handlers（避免重复配置）
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    if json_output:
        console_handler.setFormatter(JSONFormatter())
    else:
        console_handler.setFormatter(HumanFormatter())
    root_logger.addHandler(console_handler)

    # File handler with rotation
    file_handler = logging.handlers.RotatingFileHandler(
        filename=os.path.join(log_dir, "takton.log"),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(JSONFormatter())
    root_logger.addHandler(file_handler)

    # Error-specific file handler
    error_handler = logging.handlers.RotatingFileHandler(
        filename=os.path.join(log_dir, "error.log"),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(JSONFormatter())
    root_logger.addHandler(error_handler)

    # 降低第三方库的日志级别
    for noisy_logger in ("uvicorn.access", "httpx", "httpcore", "aiosqlite", "sqlalchemy.engine"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    logging.info(f"Logging initialized: dir={log_dir}, level={log_level}, json={json_output}")


def get_logger(name: str, request_id: str | None = None) -> RequestIDAdapter:
    """获取带 request_id 的 Logger

    Usage:
        logger = get_logger(__name__, request_id="xxx")
        logger.info("message", extra={"user_id": "xxx"})
    """
    logger = logging.getLogger(name)
    return RequestIDAdapter(logger, {"request_id": request_id or ""})


# 中间件：为每个请求注入 request_id
class RequestIDMiddleware:
    """FastAPI middleware to inject request_id into log records"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())[:8]
        scope["request_id"] = request_id

        async def send_with_id(message):
            await send(message)

        await self.app(scope, receive, send_with_id)