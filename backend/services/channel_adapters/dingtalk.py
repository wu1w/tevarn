"""
钉钉 Bot 适配器

参考 Hermes DingTalk adapter：
- 获取 access_token (app_key + app_secret)
- 事件回调（需公网 URL / Stream 模式）
- REST API 发送消息
- 配置：app_key, app_secret
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .base import BaseChannelAdapter

logger = logging.getLogger(__name__)

API_BASE = "https://api.dingtalk.com/v1.0"
OAPI_BASE = "https://oapi.dingtalk.com/topapi"


class DingTalkAdapter(BaseChannelAdapter):
    """钉钉 Bot 适配器"""

    platform = "dingtalk"

    def __init__(self, channel_id: str, app_key: str, app_secret: str, on_message: Any = None):
        super().__init__(channel_id, on_message)
        self._app_key = app_key
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
                    f"{OAPI_BASE}/gettoken",
                    params={"appkey": self._app_key, "appsecret": self._app_secret},
                )
                data = resp.json()
            if data.get("errcode", 0) != 0:
                raise RuntimeError(f"DingTalk token 错误: {data.get('errmsg', '')}")
            self._access_token = data["access_token"]
            self._token_expires_at = time.time() + int(data.get("expires_in", 7200))
            logger.info("DingTalk access_token 刷新成功")
            return self._access_token

    async def connect(self) -> None:
        await self._ensure_token()
        self.connected = True
        logger.info("DingTalk 连接成功 (app_key=%s)", self._app_key[:8])

    async def disconnect(self) -> None:
        self.connected = False

    async def send_text(self, chat_id: str, text: str, **kwargs) -> dict:
        token = await self._ensure_token()
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{API_BASE}/robot/oToMessages/batchSend",
                headers={"x-acs-dingtalk-access-token": token, "Content-Type": "application/json"},
                json={
                    "robotCode": self._app_key,
                    "userIds": [chat_id],
                    "msgKey": "sampleText",
                    "msgParam": "{\"content\":\"" + text[:4000].replace('"', '\\"') + "\"}",
                },
            )
            return resp.json()

    async def test_connection(self) -> tuple[bool, str]:
        try:
            await self._ensure_token()
            return True, f"✅ 连接成功 (app_key={self._app_key[:8]}...)"
        except Exception as e:
            return False, f"❌ 连接失败: {e}"
