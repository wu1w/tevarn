"""LLM HTTP 会话共享与显式超时（压测病灶 B1 修复）

病灶：各 LLM 服务每调用 `aiohttp.ClientSession()` 且**无显式超时**
（aiohttp 默认 total=300s）。provider 故障时单次调用挂 5 分钟，
叠加 cluster wait_for(300) → 系统半死（压测实测 90 个 cluster 卡死）。

修复：
1. 显式超时（可配）：
   - 非流式：total=llm_request_timeout_seconds（默认 120s）
   - 流式：total=None（长生成合法），sock_read=llm_stream_read_timeout_seconds
     （默认 300s，reasoning 停顿检测）+ connect=llm_connect_timeout_seconds
2. service 实例级共享 session：同事件循环复用（连接池生效），
   跨 loop / 已关闭自动新建（测试多 loop 场景安全）。
3. 出站代理：尊重 settings / HTTPS_PROXY（trust_env + 请求级 proxy=）。
"""
from __future__ import annotations

import asyncio
import logging
import weakref
from typing import Any
from urllib.parse import urlparse

import aiohttp

from backend.core.config import settings

logger = logging.getLogger(__name__)

# audit-fix: 登记所有共享 session（弱引用），供进程 shutdown 统一 close，
# 避免 "Unclosed client session" 与连接泄漏
_shared_sessions: "weakref.WeakSet[aiohttp.ClientSession]" = weakref.WeakSet()

# Luna/Codex reasoning 静默期下限：配置更短时仍抬到此值，避免半途 ServerTimeoutError
_MIN_STREAM_SOCK_READ = 300.0


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
    """流式调用：不限总时长（长生成合法），只卡连接与读停顿。

    Luna/Codex 等 reasoning 模型思考期可能长时间无 delta；默认 sock_read 过短
    会在「处理到一半」触发 ServerTimeoutError，表现为后端炸了。
    """
    sock = _f("llm_stream_read_timeout_seconds", 300.0)
    # 下限：即使用户/旧配置仍是 180，reasoning 也不至于半途掐断
    if sock < _MIN_STREAM_SOCK_READ:
        sock = _MIN_STREAM_SOCK_READ
    return aiohttp.ClientTimeout(
        connect=_f("llm_connect_timeout_seconds", 15.0),
        sock_read=sock,
    )


def _current_proxy_url() -> str | None:
    try:
        from backend.core.outbound_http import resolve_proxy_url, sync_proxy_env_from_settings

        # 保持进程 env 与设置页一致，供 trust_env 读取
        sync_proxy_env_from_settings()
        return resolve_proxy_url()
    except Exception:
        return None


def request_proxy_kwargs(url: str | None = None) -> dict[str, Any]:
    """给 session.get/post 用的 proxy= 参数（loopback 不走代理）。"""
    proxy = _current_proxy_url()
    if not proxy:
        return {}
    if url:
        try:
            host = (urlparse(url).hostname or "").lower()
        except Exception:
            host = ""
        if host in ("127.0.0.1", "localhost", "::1") or host.endswith(".local"):
            return {}
    # SOCKS 已打进 connector 时不应再传 proxy=
    try:
        from backend.core.outbound_http import proxy_is_socks

        if proxy_is_socks(proxy):
            return {}
    except Exception:
        pass
    return {"proxy": proxy}


def ensure_session(owner: object) -> aiohttp.ClientSession:
    """owner 级共享 aiohttp session（连接复用）

    - 同 loop、同代理、未关闭 → 复用
    - loop/代理变化 / 已关闭 → 新建
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    proxy_url = _current_proxy_url() or ""
    sess = getattr(owner, "_llm_shared_session", None)
    sess_loop = getattr(owner, "_llm_shared_session_loop", None)
    sess_proxy = getattr(owner, "_llm_shared_session_proxy", None)
    # getattr 容错：测试替身/鸭子类型 session 可能没有 .closed
    sess_closed = getattr(sess, "closed", False) if sess is not None else True
    if (
        sess is not None
        and not sess_closed
        and sess_loop is loop
        and sess_proxy == proxy_url
    ):
        return sess

    if sess is not None and not sess_closed:
        # 跨 loop / 代理变更：不强制 close（跨 loop close 会报错），仅记日志
        logger.debug(
            "recreate LLM session (loop_or_proxy_changed proxy=%s)",
            bool(proxy_url),
        )

    connector: aiohttp.BaseConnector
    try:
        from backend.core.outbound_http import proxy_is_socks

        if proxy_url and proxy_is_socks(proxy_url):
            try:
                from aiohttp_socks import ProxyConnector  # type: ignore

                connector = ProxyConnector.from_url(proxy_url)
            except ImportError:
                logger.warning(
                    "SOCKS proxy set but aiohttp-socks missing; falling back to direct"
                )
                connector = aiohttp.TCPConnector(limit=32, ttl_dns_cache=300)
        else:
            connector = aiohttp.TCPConnector(limit=32, ttl_dns_cache=300)
    except Exception:
        connector = aiohttp.TCPConnector(limit=32, ttl_dns_cache=300)

    # trust_env=True：兼容仅设了 HTTPS_PROXY 的场景；HTTP 代理另由 request_proxy_kwargs 注入
    sess = aiohttp.ClientSession(connector=connector, trust_env=True)
    try:
        _shared_sessions.add(sess)  # audit-fix: 登记以便 shutdown 统一关闭
    except Exception:
        pass
    owner._llm_shared_session = sess  # type: ignore[attr-defined]
    owner._llm_shared_session_loop = loop  # type: ignore[attr-defined]
    owner._llm_shared_session_proxy = proxy_url  # type: ignore[attr-defined]
    return sess


async def close_all_sessions() -> None:
    """audit-fix: 关闭所有登记的共享 session（lifespan shutdown 钩子）。

    单个 session close 失败（如绑定在已消亡 loop）不影响其余清理。
    """
    sessions = list(_shared_sessions)
    _shared_sessions.clear()
    for sess in sessions:
        try:
            if not getattr(sess, "closed", True):
                await sess.close()
        except Exception as e:
            logger.debug("close LLM shared session skip: %s", e)
