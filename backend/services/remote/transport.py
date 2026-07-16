"""L1 remote transport: control plane dials takton-agent WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)


class RemoteAgentError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class RemoteTransport:
    """One-shot or short-lived WS client to a takton-agent."""

    def __init__(
        self,
        url: str,
        token: str,
        *,
        timeout_s: float = 15.0,
    ):
        self.url = url
        self.token = token
        self.timeout_s = timeout_s

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        try:
            import websockets
        except ImportError as e:
            raise RemoteAgentError("DependencyMissing", "websockets not installed on control plane") from e

        params = dict(params or {})
        req_id = uuid.uuid4().hex
        hello_id = uuid.uuid4().hex
        url = self.url
        # attach token query as convenience
        if "token=" not in url and self.token:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}token={self.token}"

        try:
            async with websockets.connect(url, open_timeout=self.timeout_s, max_size=8 * 1024 * 1024) as ws:
                # hello
                await ws.send(
                    json.dumps(
                        {
                            "id": hello_id,
                            "method": "hello",
                            "params": {"token": self.token},
                        }
                    )
                )
                raw = await asyncio.wait_for(ws.recv(), timeout=self.timeout_s)
                hello = json.loads(raw)
                if not hello.get("ok"):
                    err = hello.get("error") or {}
                    raise RemoteAgentError(err.get("code") or "AuthError", err.get("message") or "hello failed")

                if method == "hello":
                    return hello.get("result")

                await ws.send(
                    json.dumps({"id": req_id, "method": method, "params": params}, ensure_ascii=False)
                )
                raw = await asyncio.wait_for(ws.recv(), timeout=self.timeout_s)
                resp = json.loads(raw)
                if not resp.get("ok"):
                    err = resp.get("error") or {}
                    raise RemoteAgentError(err.get("code") or "RemoteError", err.get("message") or "call failed")
                return resp.get("result")
        except RemoteAgentError:
            raise
        except Exception as e:
            raise RemoteAgentError("ConnectError", str(e)) from e

    async def ping(self) -> dict[str, Any]:
        t0 = time.perf_counter()
        result = await self.call("ping")
        latency_ms = int((time.perf_counter() - t0) * 1000)
        if isinstance(result, dict):
            result = {**result, "latency_ms": latency_ms}
        else:
            result = {"result": result, "latency_ms": latency_ms}
        return result


def transport_from_device_config(config: dict[str, Any] | None) -> RemoteTransport:
    cfg = config or {}
    host = cfg.get("agent_host") or cfg.get("host") or "127.0.0.1"
    port = int(cfg.get("agent_port") or cfg.get("port") or 19876)
    token = cfg.get("agent_token") or cfg.get("token") or ""
    url = cfg.get("agent_url") or f"ws://{host}:{port}"
    return RemoteTransport(url=url, token=token)


class RemoteAgentTransport(RemoteTransport):
    """兼容 skill 旧构造：host/port/token（内部转 ws URL）。"""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 19876,
        token: str = "",
        *args: Any,
        url: str | None = None,
        timeout_s: float = 15.0,
        **kwargs: Any,
    ):
        if args and not url:
            # 兼容 positional
            pass
        ws_url = url or f"ws://{host}:{int(port or 19876)}"
        super().__init__(url=ws_url, token=token or kwargs.get("token") or "", timeout_s=timeout_s)
