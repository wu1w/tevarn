"""Leader TCP client with reconnect after server restart."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from takton_code.leader.protocol import encode, decode_line
from takton_code.leader.server import read_leader_file


class LeaderClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, *, home: Path | None = None) -> None:
        self.host = host
        self.port = port
        self.home = home
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False

    @classmethod
    def from_home(cls, home: Path) -> "LeaderClient":
        meta = read_leader_file(home)
        if not meta:
            raise FileNotFoundError(f"no leader.json under {home}")
        return cls(
            host=str(meta.get("host") or "127.0.0.1"),
            port=int(meta["port"]),
            home=home,
        )

    async def connect(self) -> dict[str, Any]:
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        assert self._reader
        line = await self._reader.readline()
        msg = decode_line(line) or {}
        self._connected = True
        return msg

    async def reconnect(self, *, attempts: int = 5, delay: float = 0.4) -> dict[str, Any]:
        """Re-read leader.json (port may change after restart) and reconnect."""
        await self.close()
        last_err: Exception | None = None
        for i in range(max(1, attempts)):
            try:
                if self.home is not None:
                    meta = read_leader_file(self.home)
                    if meta:
                        self.host = str(meta.get("host") or self.host)
                        self.port = int(meta.get("port") or self.port)
                return await self.connect()
            except Exception as e:  # noqa: BLE001
                last_err = e
                await asyncio.sleep(delay * (1.0 + 0.5 * i))
        raise ConnectionError(f"leader reconnect failed: {last_err}")

    async def request(self, msg: dict[str, Any], *, retries: int = 2) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                if not self._writer or not self._reader or not self._connected:
                    await self.reconnect(attempts=3)
                assert self._writer and self._reader
                self._writer.write(encode(msg))
                await self._writer.drain()
                line = await self._reader.readline()
                if not line:
                    raise ConnectionError("leader closed connection")
                return decode_line(line) or {}
            except (ConnectionError, OSError, asyncio.IncompleteReadError, asyncio.TimeoutError) as e:
                last = e
                self._connected = False
                if attempt >= retries:
                    break
                try:
                    await self.reconnect(attempts=3)
                except Exception as e2:  # noqa: BLE001
                    last = e2
        raise ConnectionError(f"leader request failed after retries: {last}")

    async def close(self) -> None:
        self._connected = False
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None
