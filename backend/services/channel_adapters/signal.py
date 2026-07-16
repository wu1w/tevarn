"""
Signal Bot 适配器

参考 Hermes Signal adapter（via signal-cli-rest-api）：
- signal-cli-rest-api 作为中间层
- REST API 发送/接收消息
- 配置：signal_cli_url, phone_number
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import BaseChannelAdapter

logger = logging.getLogger(__name__)


class SignalAdapter(BaseChannelAdapter):
    """Signal Bot 适配器（via signal-cli-rest-api）"""

    platform = "signal"

    def __init__(self, channel_id: str, signal_cli_url: str, phone_number: str, on_message: Any = None):
        super().__init__(channel_id, on_message)
        self._signal_cli_url = signal_cli_url.rstrip("/")
        self._phone_number = phone_number
        self._polling = False

    async def connect(self) -> None:
        # 验证 signal-cli-rest-api 可达
        import httpx
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{self._signal_cli_url}/v1/about")
            if resp.status_code != 200:
                raise RuntimeError(f"signal-cli-rest-api 不可达: {resp.status_code}")
        self.connected = True
        self._polling = True
        self._listen_task = asyncio.create_task(self._poll_loop())
        logger.info("Signal 连接成功 (%s)", self._phone_number)

    async def disconnect(self) -> None:
        self._polling = False
        self.connected = False
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()

    async def _poll_loop(self):
        """轮询接收消息"""
        import httpx
        async with httpx.AsyncClient(timeout=60.0) as client:
            while self._polling:
                try:
                    resp = await client.get(
                        f"{self._signal_cli_url}/v1/receive/{self._phone_number}",
                        timeout=35.0,
                    )
                    if resp.status_code == 200:
                        messages = resp.json()
                        for msg in messages:
                            envelope = msg.get("envelope", {})
                            data_message = envelope.get("dataMessage", {})
                            if data_message:
                                await self._on_message("MESSAGE_CREATE", {
                                    "source": envelope.get("source"),
                                    "source_number": envelope.get("sourceNumber"),
                                    "timestamp": data_message.get("timestamp"),
                                    "content": data_message.get("message", ""),
                                    "group_info": data_message.get("groupInfo"),
                                })
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error("Signal poll 错误: %s", e)
                    await asyncio.sleep(5)

    async def send_text(self, chat_id: str, text: str, **kwargs) -> dict:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self._signal_cli_url}/v2/send",
                json={
                    "message": text[:4000],
                    "number": self._phone_number,
                    "recipients": [chat_id],
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def test_connection(self) -> tuple[bool, str]:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{self._signal_cli_url}/v1/about")
                if resp.status_code == 200:
                    return True, f"✅ 连接成功 ({self._phone_number})"
                return False, f"❌ signal-cli 不可达: {resp.status_code}"
        except Exception as e:
            return False, f"❌ 连接失败: {e}"
