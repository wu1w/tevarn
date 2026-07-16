"""
Telegram Bot 适配器

参考 Hermes Telegram adapter：
- 长轮询 (getUpdates) 接收消息（无需公网 IP）
- Bot API 发送消息
- 配置：bot_token
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import BaseChannelAdapter

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"


class TelegramAdapter(BaseChannelAdapter):
    """Telegram Bot 适配器（长轮询模式）"""

    platform = "telegram"

    def __init__(self, channel_id: str, token: str, on_message: Any = None):
        super().__init__(channel_id, on_message)
        self._token = token
        self._offset = 0
        self._polling = False
        self._user_agent = "TaktonTelegramBot/1.0"

    @property
    def _api_url(self) -> str:
        return f"{API_BASE}/bot{self._token}"

    async def connect(self) -> None:
        """验证 token 并开始长轮询"""
        # 验证 token
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{self._api_url}/getMe")
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram Bot token 无效: {data.get('description', '')}")
            bot_info = data.get("result", {})
            logger.info("Telegram Bot 连接成功: @%s", bot_info.get("username", "unknown"))

        self.connected = True
        self._polling = True
        self._listen_task = asyncio.create_task(self._poll_loop())

    async def disconnect(self) -> None:
        self._polling = False
        self.connected = False
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass

    async def _poll_loop(self):
        """长轮询消息"""
        import httpx
        async with httpx.AsyncClient(timeout=60.0) as client:
            while self._polling:
                try:
                    resp = await client.get(
                        f"{self._api_url}/getUpdates",
                        params={"offset": self._offset, "timeout": 30, "allowed_updates": ["message"]},
                        timeout=35.0,
                    )
                    data = resp.json()
                    if not data.get("ok"):
                        logger.error("Telegram getUpdates 错误: %s", data)
                        await asyncio.sleep(5)
                        continue

                    for update in data.get("result", []):
                        self._offset = update["update_id"] + 1
                        message = update.get("message")
                        if message:
                            await self._on_message("MESSAGE_CREATE", message)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error("Telegram poll 错误: %s", e)
                    await asyncio.sleep(5)

    async def send_text(self, chat_id: str, text: str, **kwargs) -> dict:
        """发送文本消息"""
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self._api_url}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": kwargs.get("parse_mode", "Markdown")},
            )
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram send 失败: {data.get('description', '')}")
            return data

    async def test_connection(self) -> tuple[bool, str]:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{self._api_url}/getMe")
                data = resp.json()
                if data.get("ok"):
                    bot = data["result"]
                    return True, f"✅ 连接成功: @{bot.get('username', 'unknown')}"
                return False, f"❌ Token 无效: {data.get('description', '')}"
        except Exception as e:
            return False, f"❌ 连接失败: {e}"
