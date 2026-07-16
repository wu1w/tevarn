"""mDNS helpers for L1 agent discovery (optional zeroconf)."""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any

logger = logging.getLogger(__name__)

SERVICE_TYPE = "_takton-agent._tcp.local."


async def browse_agents(timeout_ms: int = 2500) -> list[dict[str, Any]]:
    """Browse LAN for takton-agent services. Returns [] if zeroconf unavailable."""
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    except ImportError:
        logger.info("zeroconf not installed; discover returns empty")
        return []

    found: dict[str, dict[str, Any]] = {}

    class Listener(ServiceListener):
        def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            info = zc.get_service_info(type_, name)
            if not info:
                return
            addrs = []
            for a in info.addresses or []:
                try:
                    addrs.append(socket.inet_ntoa(a))
                except Exception:
                    try:
                        addrs.append(socket.inet_ntop(socket.AF_INET6, a))
                    except Exception:
                        pass
            host = addrs[0] if addrs else (info.server or "").rstrip(".")
            props = {}
            for k, v in (info.properties or {}).items():
                try:
                    kk = k.decode() if isinstance(k, bytes) else str(k)
                    vv = v.decode() if isinstance(v, bytes) else str(v)
                    props[kk] = vv
                except Exception:
                    pass
            key = f"{host}:{info.port}"
            found[key] = {
                "name": props.get("name") or name.replace("." + SERVICE_TYPE.rstrip("."), ""),
                "host": host,
                "port": int(info.port or 19876),
                "addresses": addrs,
                "properties": props,
            }

        def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            pass

        def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
            self.add_service(zc, type_, name)

    def _run() -> list[dict[str, Any]]:
        zc = Zeroconf()
        try:
            listener = Listener()
            browser = ServiceBrowser(zc, SERVICE_TYPE, listener)
            # blocking wait
            import time

            time.sleep(max(0.5, timeout_ms / 1000.0))
            browser.cancel()
            return list(found.values())
        finally:
            zc.close()

    return await asyncio.to_thread(_run)


def register_agent_service(
    *,
    name: str,
    port: int,
    properties: dict[str, str] | None = None,
):
    """Register mDNS service; returns (zeroconf, info) or (None, None)."""
    try:
        from zeroconf import ServiceInfo, Zeroconf
    except ImportError:
        return None, None

    props = {k: v.encode() if isinstance(v, str) else v for k, v in (properties or {}).items()}
    props.setdefault(b"name", name.encode())
    # addresses: all non-loopback IPv4
    addrs = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                addrs.append(socket.inet_aton(ip))
    except Exception:
        pass
    if not addrs:
        addrs = [socket.inet_aton("127.0.0.1")]

    info = ServiceInfo(
        SERVICE_TYPE,
        f"{name}.{SERVICE_TYPE}",
        addresses=addrs,
        port=port,
        properties=props,
        server=f"{socket.gethostname()}.local.",
    )
    zc = Zeroconf()
    zc.register_service(info)
    return zc, info
