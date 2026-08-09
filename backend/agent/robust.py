"""Agent 稳健性小工具：重试、续跑话术识别、瞬态错误判断。"""
from __future__ import annotations

import asyncio
import logging
import random
import re
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
    "继续推进",
    "continue",
    "resume",
    "go on",
    "keep going",
)

# 「那你接着下一项工作」类：短句续下一项（与 bare「下一步应该怎么设计」区分）
_CONTINUE_NEXT_ITEM_RE = re.compile(
    r"(?i)^[\s\u3000]{0,12}(?:"
    r"(?:那你|你|请)?接着(?:做|干)?下一项(?:工作|任务)?|"
    r"(?:请)?继续下一项(?:工作|任务)?|"
    r"(?:做|干|推进)下一项(?:工作|任务)?|"
    r"下一项工作|下一项任务|"
    r"next\s+(?:item|task)\b"
    r")[\s!！。.~～…]*$"
)


def is_continue_phrase(text: str) -> bool:
    """True for short continue / next-item phrases that should resume goal/checkpoint.

    Intentionally does **not** match long free-form work asks or plan questions
    like 「下一步应该怎么设计」.
    """
    t = (text or "").strip()
    if not t:
        return False
    # 短指令更像「续跑」；长文不当续跑
    if len(t) > 80:
        return False
    tl = t.lower()
    for p in CONTINUE_PHRASES:
        pl = p.lower()
        if tl == pl or tl.startswith(pl):
            # Avoid treating 「继续推进某功能的详细方案讨论…」as bare continue
            # when it is a long new ask — already gated by len<=80.
            return True
    if _CONTINUE_NEXT_ITEM_RE.match(t):
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
    """无可见正文（空白 / 仅不可见字符 / 仅有 thinking 块无 body）。"""
    try:
        from backend.agent.thinking_format import is_visible_empty

        return is_visible_empty(text)
    except Exception:
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
    """连续相同工具签名熔断，防止空转。

    Batch1：内部委托 DoomLoopGuard（code 移植），API 保持 observe(signatures)。
    loop 侧也可直接使用 DoomLoopGuard.record(name, args) 获得更好的参数归一。
    """

    def __init__(self, max_repeat: int = 3) -> None:
        from backend.agent.doom_loop import DoomLoopGuard

        self.max_repeat = max(2, int(max_repeat or 3))
        self._doom = DoomLoopGuard(threshold=self.max_repeat)
        self.tripped: bool = False

    def observe(self, signatures: list[str]) -> bool:
        """观察本轮签名列表。返回 True 表示刚触发熔断。"""
        if self.tripped:
            return False
        tripped_now = False
        for sig in signatures:
            if not sig:
                continue
            # signatures 已是 name|hash；整段作 name，args 空 dict
            if self._doom.record(sig, {}):
                self.tripped = True
                tripped_now = True
                break
        return tripped_now

    def observe_calls(self, calls: list[tuple[str, object]]) -> bool:
        """按 (name, arguments) 记录，参数归一更好。"""
        if self.tripped:
            return False
        tripped_now = False
        for name, args in calls:
            if self._doom.record(name or "", args):
                self.tripped = True
                tripped_now = True
                break
        return tripped_now

    def reset(self) -> None:
        self._doom.reset_turn()
        self.tripped = False

    @property
    def streak(self) -> int:
        return int(self._doom.streak)

