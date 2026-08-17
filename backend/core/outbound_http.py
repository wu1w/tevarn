"""出站 HTTP：尊重设置页代理 / 环境变量，不写死端口、不探测本机代理。

优先级（高 → 低）：
1. 设置页手动代理：outbound_proxy_enabled + host + port（Windows 风格）
2. 完整 URL 配置：settings.outbound_https_proxy / https_proxy
3. 环境变量 TEVARN_/TAKTON_HTTPS_PROXY、HTTPS_PROXY / HTTP_PROXY / ALL_PROXY
4. 无代理（直连）

用法：
    async with outbound_session(timeout=...) as (session, proxy):
        async with session.post(url, proxy=proxy, ...) as resp:
            ...

- HTTP/HTTPS 代理：通过请求级 proxy= 传递；同时 trust_env=True 兼容仅设了环境变量的场景。
- socks5/socks4：若已安装 aiohttp-socks 则用 ProxyConnector；否则返回明确错误提示。
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)

_ENV_KEYS = (
    "TEVARN_HTTPS_PROXY",
    "TEVARN_OUTBOUND_PROXY",
    "TAKTON_HTTPS_PROXY",
    "TAKTON_OUTBOUND_PROXY",
    "HTTPS_PROXY",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
)

_ALLOWED_SCHEMES = frozenset({"http", "https", "socks5", "socks5h", "socks4", "socks4a", "socks"})


def _normalize_scheme(raw: str | None) -> str:
    s = (raw or "http").strip().lower().replace("://", "")
    if s in _ALLOWED_SCHEMES:
        return "socks5" if s == "socks" else s
    return "http"


def build_proxy_url_from_parts(
    *,
    enabled: bool,
    host: str,
    port: int | str | None,
    scheme: str = "http",
) -> str | None:
    """由设置页字段拼出代理 URL；未启用或字段不全返回 None。"""
    if not enabled:
        return None
    host = (host or "").strip().strip("\"'")
    if not host:
        return None
    # 用户把完整 URL 填进地址栏时直接用
    if "://" in host:
        return host.rstrip("/")
    try:
        # 兼容 DB 里二次 JSON 编码成 '"3128"' 的情况
        port_s = str(port or 0).strip().strip("\"'")
        p = int(port_s or 0)
    except (TypeError, ValueError):
        p = 0
    if p <= 0 or p > 65535:
        return None
    sch = _normalize_scheme(str(scheme or "http").strip().strip("\"'"))
    # 去掉 host 里误带的端口
    if host.count(":") == 1 and not host.startswith("["):
        h, maybe_port = host.rsplit(":", 1)
        if maybe_port.isdigit():
            host = h
            if p <= 0:
                p = int(maybe_port)
    return f"{sch}://{host}:{p}"


def resolve_proxy_url() -> str | None:
    """解析当前应使用的出站代理 URL；无则返回 None。"""
    try:
        from backend.core.config import settings

        # 1) 设置页结构化代理（最高优先）
        structured = build_proxy_url_from_parts(
            enabled=bool(getattr(settings, "outbound_proxy_enabled", False)),
            host=str(getattr(settings, "outbound_proxy_host", "") or ""),
            port=getattr(settings, "outbound_proxy_port", 0),
            scheme=str(getattr(settings, "outbound_proxy_scheme", "http") or "http"),
        )
        if structured:
            return structured

        # 2) 完整 URL 字段
        for attr in ("outbound_https_proxy", "https_proxy"):
            v = str(getattr(settings, attr, "") or "").strip()
            if v:
                return v
    except Exception:
        pass

    # 3) 环境变量
    for key in _ENV_KEYS:
        v = (os.environ.get(key) or "").strip()
        if v:
            return v
    return None


def proxy_is_socks(proxy_url: str | None) -> bool:
    if not proxy_url:
        return False
    scheme = (urlparse(proxy_url).scheme or "").lower()
    return scheme in ("socks5", "socks5h", "socks4", "socks4a", "socks")


def _build_connector(proxy_url: str | None) -> tuple[aiohttp.BaseConnector | None, str | None, str | None]:
    """
    返回 (connector, request_proxy, error_message)。

    - HTTP 代理：connector=None，request_proxy=url（每次请求传 proxy=）
    - SOCKS：connector=ProxyConnector，request_proxy=None
    - SOCKS 但缺依赖：error_message 非空
    """
    if not proxy_url:
        return None, None, None
    if not proxy_is_socks(proxy_url):
        return None, proxy_url, None
    try:
        from aiohttp_socks import ProxyConnector  # type: ignore

        return ProxyConnector.from_url(proxy_url), None, None
    except ImportError:
        return (
            None,
            None,
            (
                f"当前代理为 SOCKS（{proxy_url}），但未安装 aiohttp-socks。"
                "请 `pip install aiohttp-socks`，或改用 HTTP 代理"
                "（Clash/V2 的 mixed/http 端口，如 http://127.0.0.1:7890）。"
            ),
        )


@asynccontextmanager
async def outbound_session(
    *,
    timeout: aiohttp.ClientTimeout | None = None,
    **session_kwargs: Any,
) -> AsyncIterator[tuple[aiohttp.ClientSession, str | None]]:
    """创建尊重全局代理的 ClientSession。

    yield: (session, request_proxy)
    - request_proxy 非空时，调用方应在每个 request 上传 proxy=request_proxy
    - SOCKS 已打进 connector 时 request_proxy 为 None
    """
    proxy_url = resolve_proxy_url()
    connector, request_proxy, socks_err = _build_connector(proxy_url)
    if socks_err:
        # 不静默直连：SOCKS 配了却用不了时，让上层拿到可读错误
        raise RuntimeError(socks_err)

    kw: dict[str, Any] = {
        "trust_env": True,
        "timeout": timeout or aiohttp.ClientTimeout(total=60),
    }
    kw.update(session_kwargs)
    if connector is not None:
        kw["connector"] = connector

    session = aiohttp.ClientSession(**kw)
    try:
        if proxy_url:
            logger.debug("outbound_http using proxy scheme=%s", urlparse(proxy_url).scheme)
        yield session, request_proxy
    finally:
        await session.close()


def sync_proxy_env_from_settings() -> str | None:
    """把当前 resolve_proxy_url() 同步到进程环境（供子进程 / trust_env）。

    返回当前生效的代理 URL（或 None）。
    """
    resolved = resolve_proxy_url()
    keys = (
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
        "https_proxy",
        "http_proxy",
        "all_proxy",
    )
    if resolved:
        for k in keys:
            os.environ[k] = resolved
    else:
        for k in keys:
            os.environ.pop(k, None)
    return resolved


def format_proxy_hint() -> str:
    """给错误消息用的代理配置提示。"""
    proxy = resolve_proxy_url()
    if proxy:
        u = urlparse(proxy)
        host = u.hostname or "…"
        port = u.port or ""
        return f"当前已配置代理 {u.scheme}://{host}:{port}，请确认代理出口可用且后端已加载设置。"
    return (
        "请在设置 → 网络/代理 中启用「使用代理服务器」并填写地址与端口"
        "（如 127.0.0.1:7890 / 3128），或设置环境变量 HTTPS_PROXY 后重启。"
    )


def format_openai_geo_error(status: int, payload: dict[str, Any] | Any, text: str) -> str:
    """把 OpenAI 地区限制等错误翻成可操作的中文说明。"""
    code = ""
    msg = ""
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            code = str(err.get("code") or err.get("type") or "")
            msg = str(err.get("message") or "")
        else:
            code = str(payload.get("code") or payload.get("error") or "")
            msg = str(payload.get("message") or payload.get("error_description") or "")
    blob = f"{code} {msg} {text}".lower()
    if (
        "unsupported_country" in blob
        or "country, region, or territory not supported" in blob
        or ("request_forbidden" in blob and "country" in blob)
    ):
        return (
            "OpenAI 拒绝了当前出口 IP 所在地区（unsupported_country_region_territory）。"
            + format_proxy_hint()
            + " 代理出口需在可用地区（如美/日/新）。"
        )
    if msg or code:
        return f"换取令牌失败 (HTTP {status}): {msg or code}"
    return f"换取令牌失败 (HTTP {status}): {(text or '')[:300]}"
