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

def is_empty_assistant_content(text: str | None) -> bool:
    """无可见正文（空白 / 仅不可见字符）。"""
    return not (text or "").strip()


def tool_call_signature(name: str, arguments: object | None) -> str:
    """稳定签名：同名 + 同参视为重复调用。"""
    import hashlib
    import json

    try:
        if isinstance(arguments, str):
            raw = arguments
        else:
            raw = json.dumps(arguments or {}, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        raw = str(arguments)
    h = hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"{name}|{h}"


def classify_tool_result_error(result: str | None) -> str | None:
    """粗分工具结果：transient / fatal / None(非错误)。"""
    t = (result or "").strip()
    if not t:
        return None
    low = t.lower()
    if not (
        t.startswith("[Error]")
        or t.startswith("[error]")
        or "timed out" in low
        or "timeout" in low
        or "失败" in t[:80]
    ):
        return None
    if any(
        k in low
        for k in (
            "timeout",
            "timed out",
            "429",
            "502",
            "503",
            "504",
            "rate limit",
            "temporarily",
            "connection reset",
            "try again",
        )
    ):
        return "transient"
    return "fatal"


class ToolRepeatGuard:
    """连续相同工具签名熔断，防止空转。"""

    def __init__(self, max_repeat: int = 3) -> None:
        self.max_repeat = max(2, int(max_repeat or 3))
        self._last_sig: str | None = None
        self._streak: int = 0
        self.tripped: bool = False

    def observe(self, signatures: list[str]) -> bool:
        """观察本轮签名列表。返回 True 表示刚触发熔断。"""
        if self.tripped:
            return False
        tripped_now = False
        for sig in signatures:
            if not sig:
                continue
            if sig == self._last_sig:
                self._streak += 1
            else:
                self._last_sig = sig
                self._streak = 1
            if self._streak >= self.max_repeat:
                self.tripped = True
                tripped_now = True
                break
        return tripped_now

    def reset(self) -> None:
        self._last_sig = None
        self._streak = 0
        self.tripped = False

