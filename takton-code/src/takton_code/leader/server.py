"""TCP leader server — 127.0.0.1 only."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Awaitable

from takton_code.leader.protocol import encode, decode_line, hello_ok
from takton_code import __version__


def leader_path(home: Path) -> Path:
    return home / "leader.json"


def write_leader_file(home: Path, host: str, port: int) -> Path:
    path = leader_path(home)
    path.write_text(
        json.dumps(
            {
                "host": host,
                "port": port,
                "pid": os.getpid(),
                "started_at": time.time(),
                "version": __version__,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def read_leader_file(home: Path) -> dict[str, Any] | None:
    path = leader_path(home)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def clear_leader_file(home: Path) -> None:
    path = leader_path(home)
    try:
        path.unlink(missing_ok=True)  # type: ignore[call-arg]
    except TypeError:
        if path.exists():
            path.unlink()
    except OSError:
        pass


class LeaderServer:
    """
    Multiplex clients onto a SessionHub-like API.
    Handlers injected to avoid circular imports.
    """

    def __init__(
        self,
        *,
        home: Path,
        list_sessions: Callable[[], Awaitable[list[dict[str, Any]]]],
        submit: Callable[[str | None, str], Awaitable[dict[str, Any]]],
        permission_reply: Callable[[str, str], Awaitable[bool]] | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("leader binds localhost only")
        self.home = home
        self.host = "127.0.0.1" if host == "localhost" else host
        self.port = port
        self.list_sessions = list_sessions
        self.submit = submit
        self.permission_reply = permission_reply
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.StreamWriter] = set()

    async def start(self) -> tuple[str, int]:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        socks = self._server.sockets or []
        if not socks:
            raise RuntimeError("leader bind failed")
        bound_port = int(socks[0].getsockname()[1])
        self.port = bound_port
        write_leader_file(self.home, self.host, bound_port)
        return self.host, bound_port

    async def stop(self) -> None:
        for w in list(self._clients):
            try:
                w.close()
            except Exception:
                pass
        self._clients.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        clear_leader_file(self.home)

    async def broadcast_event(self, session_id: str, event: dict[str, Any]) -> None:
        msg = encode({"op": "event", "session_id": session_id, "event": event})
        dead = []
        for w in self._clients:
            try:
                w.write(msg)
                await w.drain()
            except Exception:
                dead.append(w)
        for w in dead:
            self._clients.discard(w)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        # localhost only — reject non-loopback if somehow bound wider
        if peer and peer[0] not in ("127.0.0.1", "::1"):
            writer.close()
            return
        self._clients.add(writer)
        try:
            sessions = await self.list_sessions()
            writer.write(encode(hello_ok(sessions=sessions, leader_version=__version__)))
            await writer.drain()
            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break
                msg = decode_line(line)
                if not msg:
                    continue
                op = msg.get("op")
                if op == "hello":
                    sessions = await self.list_sessions()
                    writer.write(encode(hello_ok(sessions=sessions, leader_version=__version__)))
                elif op == "list_sessions":
                    sessions = await self.list_sessions()
                    writer.write(encode({"op": "sessions", "items": sessions}))
                elif op == "submit":
                    sid = msg.get("session_id")
                    text = msg.get("text") or ""
                    try:
                        result = await self.submit(sid, text)
                        writer.write(encode({"op": "submit_ok", "result": result}))
                    except Exception as e:  # noqa: BLE001
                        writer.write(encode({"op": "error", "message": str(e)}))
                elif op == "permission_reply" and self.permission_reply:
                    ok = await self.permission_reply(
                        str(msg.get("request_id") or ""),
                        str(msg.get("decision") or "deny"),
                    )
                    writer.write(encode({"op": "permission_reply_ok", "ok": ok}))
                elif op == "ping":
                    writer.write(encode({"op": "pong", "ts": time.time()}))
                else:
                    writer.write(encode({"op": "error", "message": f"unknown op {op}"}))
                await writer.drain()
        finally:
            self._clients.discard(writer)
            try:
                writer.close()
            except Exception:
                pass
