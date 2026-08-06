"""
Takton VPS Relay — reverse HTTP/WS tunnel for PC backends.

PC connects outbound via WebSocket (Bearer master token).
Phones hit public HTTP(S); traffic is multiplexed to the online tunnel.

Endpoints:
  GET  /relay/v1/health
  POST /relay/v1/register
  GET  /relay/v1/tunnels/{id}/status
  WS   /relay/v1/tunnel?token=…&tunnel_id=…
  *    /t/{tunnel_id}/…  → PC backend
  *    /…                → default online tunnel (single-user VPS)
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.websockets import WebSocketState

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("takton-relay")

RELAY_TOKEN = (os.environ.get("RELAY_TOKEN") or os.environ.get("TAKTON_RELAY_TOKEN") or "").strip()
HTTP_TIMEOUT = float(os.environ.get("RELAY_HTTP_TIMEOUT", "120"))
MAX_BODY = int(os.environ.get("RELAY_MAX_BODY", str(32 * 1024 * 1024)))

app = FastAPI(title="takton-relay", version="1.0.0")


def _check_token(auth: Optional[str], query_token: Optional[str] = None) -> bool:
    if not RELAY_TOKEN:
        logger.error("RELAY_TOKEN not set")
        return False
    if query_token and secrets.compare_digest(query_token, RELAY_TOKEN):
        return True
    if not auth:
        return False
    auth = auth.strip()
    if auth.lower().startswith("bearer "):
        auth = auth[7:].strip()
    return secrets.compare_digest(auth, RELAY_TOKEN)


@dataclass
class PendingHttp:
    future: asyncio.Future
    created: float = field(default_factory=time.time)


@dataclass
class ClientStream:
    """One public client WebSocket bridged over the PC tunnel."""

    ws: WebSocket
    opened: asyncio.Event = field(default_factory=asyncio.Event)
    closed: bool = False


@dataclass
class TunnelSession:
    tunnel_id: str
    pc_name: str
    ws: WebSocket
    connected_at: float
    last_seen: float
    pending: dict[str, PendingHttp] = field(default_factory=dict)
    # stream_id → client stream (was bare WebSocket; now carries open-ack)
    streams: dict[str, ClientStream] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def touch(self) -> None:
        self.last_seen = time.time()


class TunnelHub:
    def __init__(self) -> None:
        self._tunnels: dict[str, TunnelSession] = {}
        self._lock = asyncio.Lock()

    async def register(self, session: TunnelSession) -> None:
        async with self._lock:
            old = self._tunnels.get(session.tunnel_id)
            self._tunnels[session.tunnel_id] = session
        if old and old.ws is not session.ws:
            try:
                await old.ws.close()
            except Exception:
                pass
            for fut_wrap in list(old.pending.values()):
                if not fut_wrap.future.done():
                    fut_wrap.future.set_exception(ConnectionError("tunnel replaced"))
        logger.info("tunnel online id=%s pc=%s", session.tunnel_id, session.pc_name)

    async def unregister(self, tunnel_id: str, ws: WebSocket) -> None:
        async with self._lock:
            cur = self._tunnels.get(tunnel_id)
            if cur and cur.ws is ws:
                self._tunnels.pop(tunnel_id, None)
                session = cur
            else:
                session = None
        if session:
            for fut_wrap in list(session.pending.values()):
                if not fut_wrap.future.done():
                    fut_wrap.future.set_exception(ConnectionError("tunnel offline"))
            logger.info("tunnel offline id=%s", tunnel_id)

    def get(self, tunnel_id: str) -> Optional[TunnelSession]:
        return self._tunnels.get(tunnel_id)

    def default_tunnel(self) -> Optional[TunnelSession]:
        if len(self._tunnels) == 1:
            return next(iter(self._tunnels.values()))
        # prefer most recently seen
        if not self._tunnels:
            return None
        return max(self._tunnels.values(), key=lambda s: s.last_seen)

    def status(self, tunnel_id: str) -> dict[str, Any]:
        s = self._tunnels.get(tunnel_id)
        if not s:
            return {"ok": True, "online": False, "tunnel_id": tunnel_id}
        return {
            "ok": True,
            "online": True,
            "tunnel_id": tunnel_id,
            "pc_name": s.pc_name,
            "connected_at": s.connected_at,
            "last_seen": s.last_seen,
            "age_secs": round(time.time() - s.connected_at, 1),
        }

    def list_status(self) -> list[dict[str, Any]]:
        return [self.status(tid) for tid in self._tunnels]


HUB = TunnelHub()


@app.get("/relay/v1/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "takton-relay",
        "tunnels_online": len(HUB._tunnels),
        "token_configured": bool(RELAY_TOKEN),
    }


@app.post("/relay/v1/register")
async def register(request: Request) -> JSONResponse:
    if not _check_token(request.headers.get("authorization")):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    tunnel_id = str(body.get("tunnel_id") or "").strip() or f"pc-{secrets.token_hex(8)}"
    pc_name = str(body.get("pc_name") or "takton-pc").strip() or "takton-pc"
    return JSONResponse(
        {
            "ok": True,
            "tunnel_id": tunnel_id,
            "pc_name": pc_name,
            "ws_path": f"/relay/v1/tunnel?tunnel_id={tunnel_id}",
            "public_base": f"/t/{tunnel_id}",
            "detail": "Connect WebSocket to ws_path with same Bearer token",
        }
    )


@app.get("/relay/v1/tunnels/{tunnel_id}/status")
async def tunnel_status(tunnel_id: str, request: Request) -> JSONResponse:
    # status is semi-public for PC UI probes; still require token to avoid enum
    if not _check_token(request.headers.get("authorization")):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    return JSONResponse(HUB.status(tunnel_id))


@app.get("/relay/v1/tunnels")
async def list_tunnels(request: Request) -> JSONResponse:
    if not _check_token(request.headers.get("authorization")):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    return JSONResponse({"ok": True, "tunnels": HUB.list_status()})


@app.websocket("/relay/v1/tunnel")
async def tunnel_ws(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token") or ""
    auth = websocket.headers.get("authorization")
    if not _check_token(auth, token):
        await websocket.close(code=4401)
        return
    tunnel_id = (websocket.query_params.get("tunnel_id") or "").strip()
    if not tunnel_id:
        await websocket.close(code=4400)
        return
    pc_name = (websocket.query_params.get("pc_name") or "takton-pc").strip()

    await websocket.accept()
    session = TunnelSession(
        tunnel_id=tunnel_id,
        pc_name=pc_name,
        ws=websocket,
        connected_at=time.time(),
        last_seen=time.time(),
    )
    await HUB.register(session)
    try:
        await websocket.send_json({"type": "welcome", "tunnel_id": tunnel_id})
        while True:
            raw = await websocket.receive_text()
            session.touch()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type")
            if mtype == "http_res":
                rid = str(msg.get("id") or "")
                pend = session.pending.pop(rid, None)
                if pend and not pend.future.done():
                    pend.future.set_result(msg)
            elif mtype == "ws_opened":
                # PC finished dialing local backend — unblock client data pump.
                sid = str(msg.get("stream_id") or "")
                st = session.streams.get(sid)
                if st:
                    st.opened.set()
            elif mtype == "ws_data":
                sid = str(msg.get("stream_id") or "")
                st = session.streams.get(sid)
                if st and not st.closed and st.ws.client_state == WebSocketState.CONNECTED:
                    data_b64 = msg.get("data_b64") or ""
                    try:
                        data = base64.b64decode(data_b64)
                        # Preserve WebSocket opcode. Chat JSON is text frames;
                        # always-send_bytes broke Takton agent WS (binary rejected /
                        # clients only parse Text). frp/wstunnel keep frame type
                        # or tunnel raw TCP; we carry opcode on the mux envelope.
                        opcode = str(msg.get("opcode") or "").lower()
                        if not opcode:
                            # Legacy PC agents: best-effort UTF-8 → text
                            try:
                                text = data.decode("utf-8")
                                await st.ws.send_text(text)
                            except UnicodeDecodeError:
                                await st.ws.send_bytes(data)
                        elif opcode in ("text", "txt", "1"):
                            await st.ws.send_text(data.decode("utf-8", errors="replace"))
                        else:
                            await st.ws.send_bytes(data)
                    except Exception as e:
                        logger.debug("ws_data forward fail: %s", e)
            elif mtype == "ws_close":
                sid = str(msg.get("stream_id") or "")
                st = session.streams.pop(sid, None)
                if st:
                    st.closed = True
                    st.opened.set()  # unblock waiter if open never came
                    try:
                        if st.ws.client_state == WebSocketState.CONNECTED:
                            await st.ws.close()
                    except Exception:
                        pass
            elif mtype == "ping":
                await websocket.send_json({"type": "pong", "t": time.time()})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("tunnel ws error id=%s: %s", tunnel_id, e)
    finally:
        await HUB.unregister(tunnel_id, websocket)


async def _proxy_http(session: TunnelSession, request: Request, path: str) -> Response:
    body = await request.body()
    if len(body) > MAX_BODY:
        return JSONResponse({"ok": False, "error": "body too large"}, status_code=413)

    rid = secrets.token_hex(12)
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower()
        not in (
            "host",
            "connection",
            "transfer-encoding",
            "content-length",
            "keep-alive",
            "proxy-connection",
            "upgrade",
        )
    }
    # preserve client info
    headers["x-forwarded-for"] = request.client.host if request.client else ""
    headers["x-forwarded-proto"] = request.url.scheme
    headers["x-takton-relay"] = "1"

    q = request.url.query
    full_path = path if not q else f"{path}?{q}"
    if not full_path.startswith("/"):
        full_path = "/" + full_path

    msg = {
        "type": "http_req",
        "id": rid,
        "method": request.method,
        "path": full_path,
        "headers": headers,
        "body_b64": base64.b64encode(body).decode("ascii") if body else "",
    }
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    session.pending[rid] = PendingHttp(future=fut)
    try:
        async with session.lock:
            await session.ws.send_json(msg)
        res = await asyncio.wait_for(fut, timeout=HTTP_TIMEOUT)
    except asyncio.TimeoutError:
        session.pending.pop(rid, None)
        return JSONResponse({"ok": False, "error": "upstream timeout"}, status_code=504)
    except Exception as e:
        session.pending.pop(rid, None)
        return JSONResponse({"ok": False, "error": f"tunnel error: {e}"}, status_code=502)

    status = int(res.get("status") or 502)
    res_headers = res.get("headers") or {}
    # filter hop-by-hop
    out_headers = {
        k: v
        for k, v in res_headers.items()
        if k.lower()
        not in (
            "transfer-encoding",
            "connection",
            "keep-alive",
            "content-encoding",  # body already decoded by PC client
            "content-length",
        )
    }
    body_b64 = res.get("body_b64") or ""
    try:
        out_body = base64.b64decode(body_b64) if body_b64 else b""
    except Exception:
        out_body = b""
    return Response(content=out_body, status_code=status, headers=out_headers)


def _resolve_tunnel(tunnel_id: Optional[str]) -> Optional[TunnelSession]:
    if tunnel_id:
        return HUB.get(tunnel_id)
    return HUB.default_tunnel()


@app.api_route(
    "/t/{tunnel_id}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def proxy_scoped(tunnel_id: str, path: str, request: Request) -> Response:
    session = _resolve_tunnel(tunnel_id)
    if not session:
        return JSONResponse(
            {"ok": False, "error": "PC 未上线中继", "tunnel_id": tunnel_id},
            status_code=502,
        )
    # WebSocket upgrade cannot come through api_route; handled separately
    return await _proxy_http(session, request, "/" + path if path else "/")


@app.websocket("/t/{tunnel_id}/{path:path}")
async def proxy_ws_scoped(websocket: WebSocket, tunnel_id: str, path: str) -> None:
    session = _resolve_tunnel(tunnel_id)
    if not session:
        await websocket.close(code=1013)
        return
    await _bridge_ws(session, websocket, "/" + path if path else "/")


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def proxy_default(path: str, request: Request) -> Response:
    # don't steal relay control plane
    if path.startswith("relay/"):
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    session = _resolve_tunnel(None)
    if not session:
        return JSONResponse(
            {
                "ok": False,
                "error": "PC 未上线中继",
                "hint": "在 PC「远程连接」启用 VPS 中继，并保持 Takton 运行",
            },
            status_code=502,
        )
    return await _proxy_http(session, request, "/" + path if path else "/")


@app.websocket("/{path:path}")
async def proxy_ws_default(websocket: WebSocket, path: str) -> None:
    if path.startswith("relay/"):
        await websocket.close(code=4404)
        return
    session = _resolve_tunnel(None)
    if not session:
        await websocket.close(code=1013)
        return
    await _bridge_ws(session, websocket, "/" + path if path else "/")


async def _bridge_ws(session: TunnelSession, client_ws: WebSocket, path: str) -> None:
    """Bridge a public client WebSocket to PC local backend via tunnel mux.

    Mature reverse tunnels (frp TCP stream, wstunnel, AWS IoT tunneling) either
    forward raw bytes end-to-end or preserve frame metadata. Our JSON mux must
    carry opcode (text/binary) and wait for PC `ws_opened` before client DATA —
    otherwise early frames are dropped and chat hangs on "远端生成中".
    """
    await client_ws.accept()
    stream_id = secrets.token_hex(10)
    stream = ClientStream(ws=client_ws)
    session.streams[stream_id] = stream
    # forward query string
    q = client_ws.url.query
    full_path = path if not q else f"{path}?{q}"
    if not full_path.startswith("/"):
        full_path = "/" + full_path
    headers = {
        k: v
        for k, v in client_ws.headers.items()
        if k.lower()
        not in (
            "host",
            "connection",
            "upgrade",
            "sec-websocket-key",
            "sec-websocket-version",
            "sec-websocket-extensions",
            "sec-websocket-protocol",
            "content-length",
            "transfer-encoding",
        )
    }
    try:
        async with session.lock:
            await session.ws.send_json(
                {
                    "type": "ws_open",
                    "stream_id": stream_id,
                    "path": full_path,
                    "headers": headers,
                }
            )
        # Wait until PC dials local backend (or close). Cap so clients don't hang forever.
        try:
            await asyncio.wait_for(stream.opened.wait(), timeout=20.0)
        except asyncio.TimeoutError:
            logger.warning("ws_open timeout stream=%s path=%s", stream_id, full_path)
            try:
                await client_ws.close(code=1013)
            except Exception:
                pass
            return
        if stream.closed:
            return

        while True:
            message = await client_ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if stream.closed:
                break
            # Preserve opcode: text frames stay text on the PC side.
            if message.get("text") is not None:
                data = message["text"].encode("utf-8")
                opcode = "text"
            elif message.get("bytes") is not None:
                data = message["bytes"]
                opcode = "binary"
            else:
                continue
            async with session.lock:
                await session.ws.send_json(
                    {
                        "type": "ws_data",
                        "stream_id": stream_id,
                        "opcode": opcode,
                        "data_b64": base64.b64encode(data).decode("ascii"),
                    }
                )
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug("client ws bridge end: %s", e)
    finally:
        session.streams.pop(stream_id, None)
        stream.closed = True
        try:
            async with session.lock:
                await session.ws.send_json({"type": "ws_close", "stream_id": stream_id})
        except Exception:
            pass
        try:
            if client_ws.client_state == WebSocketState.CONNECTED:
                await client_ws.close()
        except Exception:
            pass


def main() -> None:
    import uvicorn

    host = os.environ.get("RELAY_HOST", "0.0.0.0")
    port = int(os.environ.get("RELAY_PORT", "8080"))
    if not RELAY_TOKEN:
        logger.warning("RELAY_TOKEN empty — all auth will fail")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
