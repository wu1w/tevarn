"""
In-process VPS reverse-tunnel agent.

Connects outbound WebSocket to takton-relay and serves local backend
(127.0.0.1:APP_PORT) for HTTP + WebSocket streams.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import socket
import time
from typing import Any, Optional

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

from backend.services import vps_relay as vps_cfg

logger = logging.getLogger(__name__)


def _local_backend() -> str:
    host = os.environ.get("TAKTON_APP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    port = os.environ.get("TAKTON_APP_PORT") or os.environ.get("PORT") or "8090"
    return f"http://{host}:{port}"


class VpsTunnelAgent:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._online = False
        self._last_error: Optional[str] = None
        self._connected_at: Optional[float] = None
        self._latency_ms: Optional[int] = None
        self._ws_streams: dict[str, Any] = {}
        # websockets connection is NOT concurrent-send safe; serialize all
        # relay writes (http_res / ws_data / ping / open / close).
        self._send_lock: Optional[asyncio.Lock] = None

    @property
    def online(self) -> bool:
        return self._online

    def status(self) -> dict[str, Any]:
        cfg = vps_cfg.load_config()
        return {
            "online": self._online,
            "enabled": bool(cfg.get("enabled")),
            "last_error": self._last_error,
            "connected_at": self._connected_at,
            "latency_ms": self._latency_ms,
            "local_backend": _local_backend(),
            "public_base": vps_cfg.public_base_url(cfg) if cfg.get("enabled") else None,
            "tunnel_id": cfg.get("tunnel_id"),
            "host": cfg.get("host"),
        }

    async def start(self) -> None:
        await self.stop()
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop(), name="vps-tunnel")
        logger.info("vps tunnel agent started")

    async def stop(self) -> None:
        self._stop.set()
        self._online = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self._connected_at = None
        logger.info("vps tunnel agent stopped")

    async def restart_if_enabled(self) -> None:
        cfg = vps_cfg.load_config()
        if cfg.get("enabled") and cfg.get("host") and cfg.get("master_token"):
            await self.start()
        else:
            await self.stop()

    async def _run_loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            cfg = vps_cfg.load_config()
            if not cfg.get("enabled") or not cfg.get("host") or not cfg.get("master_token"):
                self._online = False
                self._last_error = "未启用或配置不完整"
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    pass
                continue
            url = vps_cfg.ws_tunnel_url(cfg)
            if not url:
                self._last_error = "无法构造隧道 URL"
                await asyncio.sleep(3)
                continue
            try:
                await self._session(url)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._online = False
                self._last_error = str(e)
                logger.warning("vps tunnel disconnected: %s", e)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 1.7, 30.0)

    async def _relay_send(self, ws: Any, payload: dict | str) -> None:
        """Serialize all writes to the control-plane WebSocket."""
        data = payload if isinstance(payload, str) else json.dumps(payload)
        lock = self._send_lock
        if lock is None:
            await ws.send(data)
            return
        async with lock:
            await ws.send(data)

    def _spawn(self, coro: Any, name: str) -> None:
        """Fire-and-forget task that never crashes the event loop uncaught."""

        async def _runner() -> None:
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("vps tunnel task %s failed: %s", name, e)

        asyncio.create_task(_runner(), name=name)

    async def _session(self, url: str) -> None:
        # strip token from logs
        log_url = url.split("&token=")[0] + "&token=***"
        logger.info("vps tunnel connecting %s", log_url)
        self._send_lock = asyncio.Lock()
        async with websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=20,
            max_size=32 * 1024 * 1024,
            open_timeout=15,
        ) as ws:
            self._online = True
            self._connected_at = time.time()
            self._last_error = None
            logger.info("vps tunnel online")
            local = _local_backend()
            async with httpx.AsyncClient(
                base_url=local,
                timeout=httpx.Timeout(120.0, connect=10.0),
                follow_redirects=False,
            ) as client:
                while not self._stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=60.0)
                    except asyncio.TimeoutError:
                        # keepalive ping via protocol
                        try:
                            await self._relay_send(ws, {"type": "ping", "t": time.time()})
                        except Exception:
                            raise
                        continue
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    mtype = msg.get("type")
                    if mtype == "http_req":
                        # Bound concurrency: many parallel create_tasks under phone
                        # chat can exhaust FDs / memory. Cap is soft via spawn only.
                        self._spawn(self._handle_http(ws, client, msg), "http")
                    elif mtype == "ws_open":
                        self._spawn(self._handle_ws_open(ws, msg), "ws_open")
                    elif mtype == "ws_data":
                        await self._handle_ws_data(msg)
                    elif mtype == "ws_close":
                        await self._handle_ws_close(msg)
                    elif mtype in ("welcome", "pong"):
                        pass
        self._send_lock = None

    async def _handle_http(self, ws: Any, client: httpx.AsyncClient, msg: dict) -> None:
        rid = str(msg.get("id") or "")
        method = str(msg.get("method") or "GET").upper()
        path = str(msg.get("path") or "/")
        headers = msg.get("headers") or {}
        body_b64 = msg.get("body_b64") or ""
        body = base64.b64decode(body_b64) if body_b64 else b""
        # Strip hop-by-hop AND proxy identity headers.
        # Critical: uvicorn trusts X-Forwarded-For by default and rewrites
        # request.client.host. If we forward the phone's public IP, PC
        # single_user_mode auto-login returns 403 (not loopback) and pair
        # appears to "register then hang/fail". Local backend must see 127.0.0.1.
        _drop = {
            "host",
            "content-length",
            "connection",
            "transfer-encoding",
            "keep-alive",
            "proxy-connection",
            "te",
            "trailer",
            "upgrade",
            "x-forwarded-for",
            "x-forwarded-host",
            "x-forwarded-proto",
            "x-forwarded-port",
            "x-real-ip",
            "forwarded",
            "via",
            "x-takton-relay",
        }
        headers = {k: v for k, v in headers.items() if k.lower() not in _drop}
        try:
            t0 = time.perf_counter()
            r = await client.request(method, path, headers=headers, content=body)
            self._latency_ms = round((time.perf_counter() - t0) * 1000)
            # decode content for re-encode (avoid double gzip issues)
            content = r.content
            out_headers = {
                k: v
                for k, v in r.headers.items()
                if k.lower()
                not in (
                    "transfer-encoding",
                    "connection",
                    "keep-alive",
                    "content-encoding",
                    "content-length",
                )
            }
            # ensure content-type preserved
            if "content-type" not in {k.lower() for k in out_headers}:
                ct = r.headers.get("content-type")
                if ct:
                    out_headers["content-type"] = ct
            res = {
                "type": "http_res",
                "id": rid,
                "status": r.status_code,
                "headers": out_headers,
                "body_b64": base64.b64encode(content).decode("ascii"),
            }
        except Exception as e:
            logger.warning("vps tunnel http proxy fail %s %s: %s", method, path, e)
            res = {
                "type": "http_res",
                "id": rid,
                "status": 502,
                "headers": {"content-type": "application/json"},
                "body_b64": base64.b64encode(
                    json.dumps({"ok": False, "error": f"local backend: {e}"}).encode()
                ).decode("ascii"),
            }
        try:
            await self._relay_send(ws, res)
        except Exception as e:
            logger.debug("send http_res failed: %s", e)

    async def _handle_ws_open(self, relay_ws: Any, msg: dict) -> None:
        stream_id = str(msg.get("stream_id") or "")
        path = str(msg.get("path") or "/")
        headers = msg.get("headers") or {}
        local = _local_backend()
        if local.startswith("https://"):
            ws_base = "wss://" + local[len("https://") :]
        else:
            ws_base = "ws://" + local[len("http://") :]
        target = ws_base.rstrip("/") + path
        try:
            extra = []
            for k, v in headers.items():
                kl = k.lower()
                if kl in (
                    "authorization",
                    "cookie",
                    "sec-websocket-protocol",
                ):
                    extra.append((k, v))
                # never forward XFF / Real-IP into local backend (same 403 trap)
            upstream = await websockets.connect(
                target,
                additional_headers=extra or None,
                ping_interval=20,
                max_size=16 * 1024 * 1024,
                open_timeout=15,
            )
            self._ws_streams[stream_id] = upstream
            # Signal relay that client may start sending DATA (open race fix).
            await self._relay_send(
                relay_ws, {"type": "ws_opened", "stream_id": stream_id}
            )
            self._spawn(self._pump_upstream(relay_ws, stream_id, upstream), f"pump-{stream_id[:8]}")
        except Exception as e:
            logger.warning("vps tunnel ws_open fail %s: %s", path, e)
            try:
                await self._relay_send(
                    relay_ws,
                    {
                        "type": "ws_close",
                        "stream_id": stream_id,
                        "error": str(e),
                    },
                )
            except Exception:
                pass

    async def _pump_upstream(self, relay_ws: Any, stream_id: str, upstream: Any) -> None:
        """Forward local backend WS frames to public client via relay.

        Preserve opcode: Takton chat uses TEXT frames with JSON. Sending those
        as binary (old bug) made phone/Rust clients ignore deltas → 远端生成中.
        """
        try:
            async for message in upstream:
                if isinstance(message, str):
                    data = message.encode("utf-8")
                    opcode = "text"
                else:
                    data = message
                    opcode = "binary"
                await self._relay_send(
                    relay_ws,
                    {
                        "type": "ws_data",
                        "stream_id": stream_id,
                        "opcode": opcode,
                        "data_b64": base64.b64encode(data).decode("ascii"),
                    },
                )
        except ConnectionClosed:
            pass
        except Exception as e:
            logger.debug("upstream pump end: %s", e)
        finally:
            self._ws_streams.pop(stream_id, None)
            try:
                await self._relay_send(
                    relay_ws, {"type": "ws_close", "stream_id": stream_id}
                )
            except Exception:
                pass
            try:
                await upstream.close()
            except Exception:
                pass

    async def _handle_ws_data(self, msg: dict) -> None:
        stream_id = str(msg.get("stream_id") or "")
        upstream = self._ws_streams.get(stream_id)
        if not upstream:
            # Open race: data arrived before ws_open finished — drop (relay should wait).
            logger.debug("ws_data for unknown stream %s", stream_id)
            return
        data_b64 = msg.get("data_b64") or ""
        opcode = str(msg.get("opcode") or "").lower()
        try:
            data = base64.b64decode(data_b64)
            # websockets: str → text frame, bytes → binary frame.
            # Backend chat only handles TEXT; binary kills/ignores the session.
            if opcode in ("text", "txt", "1"):
                await upstream.send(data.decode("utf-8", errors="replace"))
            elif opcode in ("binary", "bin", "2"):
                await upstream.send(data)
            else:
                # Legacy relay without opcode: prefer text when UTF-8
                try:
                    await upstream.send(data.decode("utf-8"))
                except UnicodeDecodeError:
                    await upstream.send(data)
        except Exception as e:
            logger.debug("ws_data to upstream fail: %s", e)

    async def _handle_ws_close(self, msg: dict) -> None:
        stream_id = str(msg.get("stream_id") or "")
        upstream = self._ws_streams.pop(stream_id, None)
        if upstream:
            try:
                await upstream.close()
            except Exception:
                pass


_agent: Optional[VpsTunnelAgent] = None


def get_vps_tunnel() -> VpsTunnelAgent:
    global _agent
    if _agent is None:
        _agent = VpsTunnelAgent()
    return _agent
