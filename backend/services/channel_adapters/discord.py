"""
Discord Bot 适配器

参考 Hermes Discord adapter：
- WebSocket Gateway 连接
- REST API 发送消息
- 配置：bot_token
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import BaseChannelAdapter

logger = logging.getLogger(__name__)

API_BASE = "https://discord.com/api/v10"
GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"


class DiscordAdapter(BaseChannelAdapter):
    """Discord Bot 适配器（WebSocket Gateway）"""

    platform = "discord"

    def __init__(self, channel_id: str, token: str, on_message: Any = None):
        super().__init__(channel_id, on_message)
        self._token = token
        self._session_id = None
        self._seq = None
        self._ws = None
        self._aiohttp_session = None
        self._heartbeat_task = None
        self._running = False

    async def connect(self) -> None:
        import aiohttp
        self._aiohttp_session = aiohttp.ClientSession()
        self._ws = await self._aiohttp_session.ws_connect(GATEWAY_URL)
        self._running = True

        # HELLO
        msg = await asyncio.wait_for(self._ws.receive_json(), timeout=20)
        heartbeat_interval = msg["d"]["heartbeat_interval"] / 1000
        logger.info("Discord HELLO, heartbeat=%.1fs", heartbeat_interval)

        # IDENTIFY
        await self._ws.send_json({
            "op": 2,
            "d": {
                "token": self._token,
                "intents": 524288,  # GUILD_MESSAGES (1<<9) + MESSAGE_CONTENT (1<<15)
                "properties": {"os": "linux", "browser": "takton", "device": "takton"},
            },
        })

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(heartbeat_interval))
        self._listen_task = asyncio.create_task(self._listen_loop())
        self.connected = True
        logger.info("Discord WebSocket 连接成功")

    async def disconnect(self) -> None:
        self._running = False
        self.connected = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._aiohttp_session and not self._aiohttp_session.closed:
            await self._aiohttp_session.close()

    async def _heartbeat_loop(self, interval: float):
        try:
            while self._running and self._ws and not self._ws.closed:
                await asyncio.sleep(interval)
                if self._running and self._ws and not self._ws.closed:
                    await self._ws.send_json({"op": 1, "d": self._seq})
        except Exception as e:
            logger.error("Discord heartbeat 错误: %s", e)

    async def _listen_loop(self):
        import aiohttp
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = msg.json()
                    op = data.get("op")
                    t = data.get("t")
                    d = data.get("d")
                    s = data.get("s")
                    if s: self._seq = s

                    if op == 0:  # DISPATCH
                        if t == "READY":
                            self._session_id = d.get("session_id")
                            user = d.get("user", {})
                            logger.info("Discord READY: %s#%s", user.get("username"), user.get("discriminator"))
                        elif t == "MESSAGE_CREATE":
                            # 忽略 bot 自己的消息
                            author = d.get("author", {})
                            if author.get("bot"):
                                continue
                            await self._on_message("MESSAGE_CREATE", d)
                    elif op == 7:  # RECONNECT
                        logger.warning("Discord 要求重连")
                        self._running = False
                        break
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        except Exception as e:
            logger.error("Discord listen 错误: %s", e)
        finally:
            self._running = False
            self.connected = False

    async def send_text(self, chat_id: str, text: str, **kwargs) -> dict:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{API_BASE}/channels/{chat_id}/messages",
                headers={"Authorization": f"Bot {self._token}", "Content-Type": "application/json"},
                json={"content": text[:2000]},
            )
            resp.raise_for_status()
            return resp.json()

    async def test_connection(self) -> tuple[bool, str]:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{API_BASE}/users/@me",
                    headers={"Authorization": f"Bot {self._token}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return True, f"✅ 连接成功: {data.get('username', 'unknown')}"
                return False, f"❌ Token 无效: {resp.status_code}"
        except Exception as e:
            return False, f"❌ 连接失败: {e}"
