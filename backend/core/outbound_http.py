"""出站 HTTP：尊重用户常规全局代理，不写死端口/不探测本机代理。

优先级（高 → 低）：
1. 配置/环境变量 TEVARN_HTTPS_PROXY、TEVARN_OUTBOUND_PROXY（显式覆盖）
2. 标准环境变量 HTTPS_PROXY / HTTP_PROXY / ALL_PROXY（及小写同名）
3. 无代理（直连）

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
    "HTTPS_PROXY",
    "https_proxy",
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
)


def resolve_proxy_url() -> str | None:
    """解析当前应使用的出站代理 URL；无则返回 None。"""
    try:
        from backend.core.config import settings

        for attr in ("outbound_https_proxy", "https_proxy"):
            v = str(getattr(settings, attr, "") or "").strip()
            if v:
                return v
    except Exception:
        pass

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
        proxy = resolve_proxy_url()
        if proxy:
            return (
                "OpenAI 拒绝了当前出口 IP 所在地区（unsupported_country_region_territory）。"
                f"已检测到代理设置（{urlparse(proxy).scheme}://…），"
                "请确认代理出口在可用地区（如美/日/新），且 Tevarn 后端进程已加载该环境变量后重启。"
            )
        return (
            "OpenAI 拒绝了当前地区（unsupported_country_region_territory）。"
            "浏览器能登录不代表后端换 token 可用：请为本机/后端配置常规全局代理后重启 Tevarn，例如：\n"
            "  • 环境变量 HTTPS_PROXY / HTTP_PROXY / ALL_PROXY\n"
            "  • 或 TEVARN_HTTPS_PROXY=http://127.0.0.1:<你的代理端口>\n"
            "Clash/V2Ray 等请开启系统代理或复制 mixed 端口的 HTTP 代理地址。"
        )
    if msg or code:
        return f"换取令牌失败 (HTTP {status}): {msg or code}"
    return f"换取令牌失败 (HTTP {status}): {(text or '')[:300]}"
