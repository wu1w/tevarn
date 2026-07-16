"""Agent 稳健性小工具：重试、续跑话术识别、瞬态错误判断。"""
from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

CONTINUE_PHRASES = (
    "请继续",
    "继续",
    "接着做",
    "接着干",
    "继续完成",
    "continue",
    "resume",
    "go on",
    "keep going",
)


def is_continue_phrase(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    # 短指令更像「续跑」；长文不当续跑
    if len(t) > 80:
        return False
    for p in CONTINUE_PHRASES:
        if t == p.lower() or t.startswith(p.lower()):
            return True
    return False


def is_transient_llm_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    keys = (
        "timeout",
        "timed out",
        "temporarily",
        "429",
        "502",
        "503",
        "504",
        "connection reset",
        "connection refused",
        "server disconnected",
        "cloudflare",
        "rate limit",
        "overloaded",
        "try again",
        "econnreset",
        "network",
    )
    return any(k in msg for k in keys)


async def async_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 0.8,
    max_delay: float = 8.0,
    retry_if: Callable[[BaseException], bool] | None = None,
    label: str = "op",
) -> T:
    """简单指数退避重试。"""
    last: BaseException | None = None
    n = max(1, attempts)
    for i in range(n):
        try:
            return await fn()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last = e
            if i >= n - 1:
                break
            if retry_if is not None and not retry_if(e):
                break
            delay = min(max_delay, base_delay * (2**i))
            delay *= 0.7 + random.random() * 0.6
            logger.warning(
                "%s failed (%s/%s): %s; retry in %.1fs",
                label,
                i + 1,
                n,
                e,
                delay,
            )
            await asyncio.sleep(delay)
    assert last is not None
    raise last
