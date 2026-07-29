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


# ─────────────────────────────────────────────────────────────────────
# Agent 工具（http / browser）的分层策略
#
# validate_public_url 拦掉一切私网+回环，这对「服务端代用户拉外链」是对的，
# 但**本地优先场景下直接套用会立刻惹恼用户**：让 Agent 看一眼自己跑在
# localhost:3000 的项目、读一下 NAS 上的文件、调一下路由器接口，都是完全正当
# 的日常需求，而且是这类产品的核心价值。
#
# 所以对 Agent 工具分两层：
#   硬拦：云厂商元数据端点 —— 这些地址在个人电脑上永远没有正当用途，
#         一旦 Agent 被提示词注入诱导去读，泄露的是云凭证。
#   放行 + 记审计：私网 / 回环 —— 正常用法，但记下来以便事后追溯。
#
# 想要服务器级严格度的人：设 agent_block_private_network=true。
# ─────────────────────────────────────────────────────────────────────

# 云厂商实例元数据服务（IMDS）。个人机器上访问这些只可能是被骗了。
_METADATA_HOSTS = frozenset(
    {
        "169.254.169.254",  # AWS / Azure / GCP / OpenStack / DigitalOcean
        "fd00:ec2::254",  # AWS IMDSv2 over IPv6
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",  # AWS 传统别名
        "100.100.100.200",  # 阿里云
        "169.254.169.253",  # AWS DNS
    }
)


def _resolved_ips(hostname: str) -> list[str]:
    try:
        return [info[4][0] for info in socket.getaddrinfo(hostname, None)]
    except socket.gaierror:
        return []


def check_agent_url(url: str) -> tuple[bool, str]:
    """Agent 的 http / browser 工具专用的分层准入。

    Returns:
        (allowed, note) —— note 非空时是拒绝理由，或需要记审计的说明。
    """
    if not url:
        return False, "URL is required"

    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return False, f"Unsupported scheme: {parsed.scheme or '(empty)'}"

    hostname = (parsed.hostname or "").strip()
    if not hostname:
        return False, "URL must include a hostname"

    lowered = hostname.lower()

    # 第 1 层：云元数据端点，无条件拦。别名和解析后的 IP 都要查，
    # 免得用一个指向 169.254.169.254 的自有域名绕过去。
    candidates = {lowered}
    try:
        ipaddress.ip_address(lowered)
    except ValueError:
        candidates.update(ip.lower() for ip in _resolved_ips(lowered))
    if candidates & _METADATA_HOSTS:
        return False, (
            f"Blocked: '{hostname}' is a cloud instance metadata endpoint. "
            f"这类地址在个人设备上没有正当用途，访问它通常意味着 Agent 正被"
            f"提示词注入诱导去读取云凭证。"
        )

    # 第 2 层：私网 / 回环 —— 默认放行（本地优先的核心用法），可选收紧
    is_internal = False
    try:
        ipaddress.ip_address(lowered)
        is_internal = _is_private_ip(lowered)
    except ValueError:
        ips = _resolved_ips(lowered)
        is_internal = bool(ips) and any(_is_private_ip(ip) for ip in ips)

    if is_internal:
        try:
            from backend.core.config import settings

            strict = bool(getattr(settings, "agent_block_private_network", False))
        except Exception:
            strict = False
        if strict:
            return False, (
                f"Blocked: '{hostname}' is on a private/loopback network and "
                f"agent_block_private_network is enabled."
            )
        return True, f"internal-network access: {hostname}"

    return True, ""
