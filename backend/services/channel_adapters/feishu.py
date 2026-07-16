"""
飞书 Bot 适配器

参考 Hermes Feishu adapter：
- 获取 tenant_access_token (app_id + app_secret)
- 事件回调（需公网 URL）或 WebSocket 长连接
- REST API 发送消息
- 配置：app_id, app_secret
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .base import BaseChannelAdapter

logger = logging.getLogger(__name__)

API_BASE = "https://open.feishu.cn/open-apis"


class FeishuAdapter(BaseChannelAdapter):
    """飞书 Bot 适配器"""

    platform = "feishu"

    def __init__(self, channel_id: str, app_id: str, app_secret: str, on_message: Any = None):
        super().__init__(channel_id, on_message)
        self._app_id = app_id
        self._app_secret = app_secret
        self._access_token = None
        self._token_expires_at = 0
        self._token_lock = asyncio.Lock()

    async def _ensure_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token
        async with self._token_lock:
            import httpx
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{API_BASE}/auth/v3/tenant_access_token/internal",
                    json={"app_id": self._app_id, "app_secret": self._app_secret},
                )
                data = resp.json()
            if data.get("code", 0) != 0:
                raise RuntimeError(f"Feishu token 错误: {data.get('msg', '')}")
            self._access_token = data["tenant_access_token"]
            self._token_expires_at = time.time() + int(data.get("expire", 7200))
            logger.info("Feishu tenant_access_token 刷新成功")
            return self._access_token

    async def connect(self) -> None:
        await self._ensure_token()
        self.connected = True
        logger.info("Feishu 连接成功 (app_id=%s)", self._app_id[:8])

    async def disconnect(self) -> None:
        self.connected = False

    async def send_text(self, chat_id: str, text: str, **kwargs) -> dict:
        token = await self._ensure_token()
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{API_BASE}/im/v1/messages",
                params={"receive_id_type": kwargs.get("receive_id_type", "chat_id")},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "receive_id": chat_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text[:4000]}),
                },
            )
            data = resp.json()
            if data.get("code", 0) != 0:
                raise RuntimeError(f"Feishu send 错误: {data.get('msg', '')}")
            return data

    async def test_connection(self) -> tuple[bool, str]:
        try:
            await self._ensure_token()
            return True, f"✅ 连接成功 (app_id={self._app_id[:8]}...)"
        except Exception as e:
            return False, f"❌ 连接失败: {e}"


import json
