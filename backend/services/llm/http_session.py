"""LLM HTTP 会话共享与显式超时（压测病灶 B1 修复）

病灶：各 LLM 服务每调用 `aiohttp.ClientSession()` 且**无显式超时**
（aiohttp 默认 total=300s）。provider 故障时单次调用挂 5 分钟，
叠加 cluster wait_for(300) → 系统半死（压测实测 90 个 cluster 卡死）。

修复：
1. 显式超时（可配）：
   - 非流式：total=llm_request_timeout_seconds（默认 120s）
   - 流式：total=None（长生成合法），sock_read=llm_stream_read_timeout_seconds
     （默认 180s，停顿检测）+ connect=llm_connect_timeout_seconds（默认 10s）
2. service 实例级共享 session：同事件循环复用（连接池生效），
   跨 loop / 已关闭自动新建（测试多 loop 场景安全）。
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp

from backend.core.config import settings

logger = logging.getLogger(__name__)


def _f(key: str, default: float) -> float:
    try:
        return float(getattr(settings, key, default) or default)
    except (TypeError, ValueError):
        return default


def request_timeout() -> aiohttp.ClientTimeout:
    """非流式调用：总时限 + 连接时限"""
    return aiohttp.ClientTimeout(
        total=_f("llm_request_timeout_seconds", 120.0),
        connect=_f("llm_connect_timeout_seconds", 10.0),
    )


def stream_timeout() -> aiohttp.ClientTimeout:
    """流式调用：不限总时长（长生成合法），只卡连接与读停顿"""
    return aiohttp.ClientTimeout(
        connect=_f("llm_connect_timeout_seconds", 10.0),
        sock_read=_f("llm_stream_read_timeout_seconds", 180.0),
    )


def ensure_session(owner: object) -> aiohttp.ClientSession:
    """owner 级共享 aiohttp session（连接复用）

    - 同 loop 且未关闭 → 复用
    - loop 变化 / 已关闭 → 新建（旧 session 交由 GC，避免跨 loop close 报错）
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    sess = getattr(owner, "_llm_shared_session", None)
    sess_loop = getattr(owner, "_llm_shared_session_loop", None)
    # getattr 容错：测试替身/鸭子类型 session 可能没有 .closed
    sess_closed = getattr(sess, "closed", False) if sess is not None else True
    if sess is not None and not sess_closed and sess_loop is loop:
        return sess

    if sess is not None and not sess_closed:
        # 跨 loop 废弃：不 close（跨 loop close 会报错），仅记日志
        logger.debug("abandon LLM session bound to a different event loop")

    connector = aiohttp.TCPConnector(limit=32, ttl_dns_cache=300)
    sess = aiohttp.ClientSession(connector=connector)
    owner._llm_shared_session = sess  # type: ignore[attr-defined]
    owner._llm_shared_session_loop = loop  # type: ignore[attr-defined]
    return sess
