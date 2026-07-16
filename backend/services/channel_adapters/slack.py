"""
Slack Bot 适配器

参考 Hermes Slack adapter：
- Socket Mode (WebSocket) 接收消息（无需公网）
- Web API 发送消息
- 配置：bot_token, app_token (Socket Mode)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .base import BaseChannelAdapter

logger = logging.getLogger(__name__)

API_BASE = "https://slack.com/api"


class SlackAdapter(BaseChannelAdapter):
    """Slack Bot 适配器（Socket Mode）"""

    platform = "slack"

    def __init__(self, channel_id: str, bot_token: str, app_token: str = "", on_message: Any = None):
        super().__init__(channel_id, on_message)
        self._bot_token = bot_token
        self._app_token = app_token
        self._ws = None
        self._aiohttp_session = None
        self._running = False

    async def connect(self) -> None:
        if not self._app_token:
            # 无 Socket Mode token，使用 RTM 或纯 Web API 模式
            self.connected = True
            logger.info("Slack 连接成功 (Web API 模式)")
            return

        # Socket Mode: 获取 WebSocket URL
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{API_BASE}/apps.connections.open",
                headers={"Authorization": f"Bearer {self._app_token}", "Content-Type": "application/json"},
            )
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Slack Socket Mode 错误: {data.get('error', '')}")
            ws_url = data["url"]

        import aiohttp
        self._aiohttp_session = aiohttp.ClientSession()
        self._ws = await self._aiohttp_session.ws_connect(ws_url)
        self._running = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        self.connected = True
        logger.info("Slack Socket Mode 连接成功")

    async def disconnect(self) -> None:
        self._running = False
        self.connected = False
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._aiohttp_session and not self._aiohttp_session.closed:
            await self._aiohttp_session.close()

    async def _listen_loop(self):
        import aiohttp
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("type") == "events_api":
                        payload = data.get("payload", {})
                        event = payload.get("event", {})
                        if event.get("type") == "message" and not event.get("bot_id"):
                            await self._on_message("MESSAGE_CREATE", event)
                        # ACK
                        await self._ws.send_json({"envelope_id": data.get("envelope_id")})
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        except Exception as e:
            logger.error("Slack listen 错误: %s", e)
        finally:
            self._running = False
            self.connected = False

    async def send_text(self, chat_id: str, text: str, **kwargs) -> dict:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{API_BASE}/chat.postMessage",
                headers={"Authorization": f"Bearer {self._bot_token}", "Content-Type": "application/json"},
                json={"channel": chat_id, "text": text[:40000]},
            )
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Slack send 错误: {data.get('error', '')}")
            return data

    async def test_connection(self) -> tuple[bool, str]:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{API_BASE}/auth.test",
                    headers={"Authorization": f"Bearer {self._bot_token}"},
                )
                data = resp.json()
                if data.get("ok"):
                    return True, f"✅ 连接成功: {data.get('user', 'unknown')} @ {data.get('team', 'unknown')}"
                return False, f"❌ Token 无效: {data.get('error', '')}"
        except Exception as e:
            return False, f"❌ 连接失败: {e}"
