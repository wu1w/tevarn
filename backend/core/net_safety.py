"""
网络请求安全校验工具
用于防御 SSRF（服务端请求伪造）：禁止访问内网/回环/链路本地地址
供 Skills、Tools、WorkflowEngine 等所有发起出站 HTTP 请求的地方复用
"""

import ipaddress
import socket
from urllib.parse import urlparse

# 明确禁止访问的主机名（不区分大小写）
_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata.google.internal",
}

_ALLOWED_SCHEMES = {"http", "https"}


class UnsafeURLError(ValueError):
    """当 URL 指向不安全的目标（内网/回环/链路本地地址）时抛出"""


def _is_private_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # 无法解析为合法 IP，保守地视为不安全
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_public_url(url: str) -> None:
    """
    校验 URL 是否安全可以被服务端发起请求。

    校验规则：
    1. scheme 必须是 http/https
    2. 必须提供 hostname
    3. hostname 不在黑名单内
    4. hostname 解析出的所有 IP 地址都不能是私有/回环/链路本地/保留地址

    Raises:
        UnsafeURLError: 当 URL 不安全时
    """
    if not url:
        raise UnsafeURLError("URL is required")

    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(f"Unsupported scheme: {parsed.scheme or '(empty)'}")

    hostname = parsed.hostname
    if not hostname:
        raise UnsafeURLError("URL must include a hostname")

    if hostname.lower() in _BLOCKED_HOSTNAMES:
        raise UnsafeURLError(f"Access to '{hostname}' is not allowed")

    # 直接是 IP 地址的情况
    try:
        ipaddress.ip_address(hostname)
        if _is_private_ip(hostname):
            raise UnsafeURLError(f"Access to private/internal address '{hostname}' is not allowed")
        return
    except ValueError:
        pass  # hostname 不是纯 IP，走 DNS 解析校验

    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as e:
        raise UnsafeURLError(f"Failed to resolve host '{hostname}': {e}") from e

    for info in infos:
        ip_str = info[4][0]
        if _is_private_ip(ip_str):
            raise UnsafeURLError(
                f"Host '{hostname}' resolves to private/internal address '{ip_str}', access blocked"
            )
