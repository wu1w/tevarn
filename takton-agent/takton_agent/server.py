from __future__ import annotations

import argparse
import os
import asyncio
import json
import logging
import platform
import secrets
import socket
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from .protocol import DEFAULT_AGENT_PORT, PROTOCOL_VERSION
from .services.exec_service import ExecService
from .services.file_service import FileService

logger = logging.getLogger("takton-agent")


class AgentSession:
    def __init__(self, token: str, root: str, name: str):
        self.token = token
        self.root = root
        self.name = name
        self.files = FileService(root)
        self.exec = ExecService(root)
        self.started = time.time()

    def capabilities(self) -> list[str]:
        return ["file.list", "file.read", "exec.run", "ping"]

    async def handle(self, msg: dict[str, Any]) -> dict[str, Any]:
        req_id = str(msg.get("id") or uuid.uuid4())
        method = (msg.get("method") or "").strip()
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            params = {}

        try:
            if method == "hello":
                tok = params.get("token") or ""
                if tok != self.token:
                    raise PermissionError("invalid token")
                return self._ok(
                    req_id,
                    {
                        "protocol": PROTOCOL_VERSION,
                        "name": self.name,
                        "hostname": socket.gethostname(),
                        "platform": platform.platform(),
                        "root": str(Path(self.root).resolve()),
                        "capabilities": self.capabilities(),
                    },
                )
            if method == "ping":
                return self._ok(
                    req_id,
                    {"pong": True, "ts": time.time(), "uptime_s": time.time() - self.started},
                )
            if method == "capabilities":
                return self._ok(req_id, {"capabilities": self.capabilities()})
            if method == "file.list":
                return self._ok(req_id, self.files.list_dir(params.get("path") or "."))
            if method == "file.read":
                return self._ok(
                    req_id,
                    self.files.read_file(
                        params.get("path") or "",
                        max_bytes=int(params.get("max_bytes") or 256_000),
                    ),
                )
            if method == "exec.run":
                result = await self.exec.run(
                    params.get("command") or "",
                    cwd=params.get("cwd"),
                )
                return self._ok(req_id, result)
            raise ValueError(f"unknown method: {method}")
        except Exception as e:
            code = type(e).__name__
            return {
                "id": req_id,
                "ok": False,
                "error": {"code": code, "message": str(e)},
            }

    @staticmethod
    def _id(req_id: str) -> str:
        return req_id

    @staticmethod
    def _ok(req_id: str, result: Any) -> dict[str, Any]:
        return {"id": req_id, "ok": True, "result": result}


async def _handle_ws(websocket, session: AgentSession, authed: dict[str, bool]):
    # websockets library API
    async for raw in websocket:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await websocket.send(
                json.dumps(
                    {
                        "id": "",
                        "ok": False,
                        "error": {"code": "BadJson", "message": "invalid json"},
                    }
                )
            )
            continue
        method = msg.get("method")
        if not authed["ok"]:
            # allow token on first hello OR any method with params.token
            token = (msg.get("params") or {}).get("token")
            if method == "hello":
                resp = await session.handle(msg)
                if resp.get("ok"):
                    authed["ok"] = True
                await websocket.send(json.dumps(resp, ensure_ascii=False))
                continue
            if token == session.token:
                authed["ok"] = True
            else:
                await websocket.send(
                    json.dumps(
                        {
                            "id": msg.get("id"),
                            "ok": False,
                            "error": {"code": "Unauthorized", "message": "auth required"},
                        }
                    )
                )
                continue
        resp = await session.handle(msg)
        await websocket.send(json.dumps(resp, ensure_ascii=False))


async def run_server(host: str, port: int, token: str, root: str, name: str):
    try:
        import websockets
    except ImportError as e:
        raise SystemExit(
            "takton-agent requires 'websockets'. pip install websockets"
        ) from e

    session = AgentSession(token=token, root=root, name=name)

    async def handler(ws):
        # optional query token
        authed = {"ok": False}
        try:
            path = ws.request.path  # websockets 12+
        except Exception:
            path = ""
        if path and "token=" in path:
            from urllib.parse import parse_qs, urlparse

            q = parse_qs(urlparse(path).query)
            if (q.get("token") or [None])[0] == token:
                authed["ok"] = True
        logger.info("client connected authed=%s", authed["ok"])
        try:
            await _handle_ws(ws, session, authed)
        finally:
            logger.info("client disconnected")

    logger.info(
        "takton-agent listening ws://%s:%s root=%s name=%s",
        host,
        port,
        root,
        name,
    )
    # optional mDNS (_takton-agent._tcp.local)
    zc = info = None
    try:
        from zeroconf import ServiceInfo, Zeroconf
        import socket as _sock

        addrs = []
        try:
            for ai in _sock.getaddrinfo(_sock.gethostname(), None, _sock.AF_INET):
                ip = ai[4][0]
                if not str(ip).startswith("127."):
                    addrs.append(_sock.inet_aton(ip))
        except Exception:
            pass
        if not addrs:
            addrs = [_sock.inet_aton("127.0.0.1")]
        stype = "_takton-agent._tcp.local."
        info = ServiceInfo(
            stype,
            f"{name}.{stype}",
            addresses=addrs,
            port=port,
            properties={"name": name.encode(), "proto": b"1"},
            server=f"{_sock.gethostname()}.local.",
        )
        zc = Zeroconf()
        zc.register_service(info)
        logger.info("mDNS registered %s port=%s", name, port)
    except Exception as e:
        logger.info("mDNS not available: %s", e)

    try:
        async with websockets.serve(handler, host, port, max_size=8 * 1024 * 1024):
            await asyncio.Future()
    finally:
        if zc is not None and info is not None:
            try:
                zc.unregister_service(info)
                zc.close()
            except Exception:
                pass


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="takton-agent", description="Takton remote agent (L1)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_AGENT_PORT)
    parser.add_argument("--token", default=os.environ.get("TAKTON_AGENT_TOKEN") or "")
    parser.add_argument(
        "--root",
        default=os.environ.get("TAKTON_AGENT_ROOT") or str(Path.home() / "projects"),
    )
    parser.add_argument(
        "--name",
        default=os.environ.get("TAKTON_AGENT_NAME") or socket.gethostname(),
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    if not args.token:
        args.token = secrets.token_urlsafe(18)
        print(f"[takton-agent] generated token: {args.token}", file=sys.stderr)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    print(
        f"[takton-agent] name={args.name} ws://{args.host}:{args.port} root={args.root}",
        file=sys.stderr,
    )
    print(f"[takton-agent] token={args.token}", file=sys.stderr)
    asyncio.run(run_server(args.host, args.port, args.token, args.root, args.name))



if __name__ == "__main__":
    main()
