"""
消息通道适配器基类

所有平台适配器的公共接口，参考 Hermes BasePlatformAdapter。
子类需实现：connect(), disconnect(), send_message(), _listen_loop()
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Awaitable, Optional

logger = logging.getLogger(__name__)


class BaseChannelAdapter(ABC):
    """通道适配器基类"""

    platform: str = "unknown"
    connected: bool = False

    def __init__(
        self,
        channel_id: str,
        on_message: Callable[[str, dict], Awaitable[None]],
    ):
        """
        :param channel_id: 通道 DB ID
        :param on_message: 消息回调 (event_type, data) -> None
        """
        self.channel_id = channel_id
        self._on_message = on_message
        self._listen_task: asyncio.Task | None = None

    @abstractmethod
    async def connect(self) -> None:
        """建立连接。成功后 self.connected = True。"""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接。"""
        self.connected = False
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()

    @abstractmethod
    async def send_text(self, chat_id: str, text: str, **kwargs) -> dict:
        """
        发送文本消息。

        :param chat_id: 目标 chat_id（含义因平台而异）
        :param text: 消息文本
        :return: 平台原始响应
        """
        ...

    async def test_connection(self) -> tuple[bool, str]:
        """
        测试连接是否可用。
        返回 (success, message)。
        """
        try:
            await self.connect()
            await self.disconnect()
            return True, "✅ 连接成功"
        except Exception as e:
            return False, f"❌ 连接失败: {e}"

    def _emit_message(self, event_type: str, data: dict):
        """触发消息回调（fire-and-forget）。"""
        asyncio.create_task(self._on_message(event_type, data))
