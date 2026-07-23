"""Takton 统一时间源

所有「获取当前时间」的调用必须从这里走，禁止裸用 datetime.now() / utcnow()。

约定：
- 存储 / 计算统一用 UTC（timezone-aware）
- 用户可见的展示（日志可读输出、注入 LLM 的当前时间）用系统本地时区
- naive datetime 一律视为 UTC（防御性处理，SQLite 读出时会丢 tzinfo）

注意：本模块零项目内依赖，core/models/agent 任何层 import 都不会形成循环。
"""

from __future__ import annotations

from datetime import datetime, timezone


def now() -> datetime:
    """当前 UTC 时间（timezone-aware）。"""
    return datetime.now(timezone.utc)


def utc_now() -> datetime:
    """同 now()，语义更明确，用于 ORM default / 数据落库。"""
    return datetime.now(timezone.utc)


def local_now() -> datetime:
    """当前本地时间（跟随系统时区），用于人可读的日志与提示词。"""
    return datetime.now(timezone.utc).astimezone()


def ensure_aware(dt: datetime) -> datetime:
    """确保 datetime 是 timezone-aware；naive 一律视为 UTC。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def iso_utc(dt: datetime) -> str:
    """ISO 8601 UTC 字符串（Z 后缀，前端 new Date() 可正确解析）。"""
    return ensure_aware(dt).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
