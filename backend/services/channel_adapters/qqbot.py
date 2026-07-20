"""
QQ Bot 适配器 — 轻量版

基于 Hermes QQBot adapter 核心逻辑，为 Takton 提供 QQ Channel 连接能力。
支持：获取 access_token、WebSocket Gateway 连接、收发消息。

配置字段（存于 channels 表 extra JSON）：
  app_id: QQ Bot AppID
  client_secret: QQ Bot AppSecret
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from .base import BaseChannelAdapter
import aiohttp

logger = logging.getLogger(__name__)

# ─── 常量 ──────────────────────────────────────────────────────

API_BASE = "https://api.sgroup.qq.com"
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
GATEWAY_URL_PATH = "/gateway"

# QQ Bot opcode
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RESUME = 6
OP_RECONNECT = 7
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

# Intents: 公域机器人默认权限
INTENT_PUBLIC_GUILD_MESSAGES = 1 << 30  # 频道消息
INTENT_GUILD_MESSAGES = 1 << 9          # 私域频道消息
INTENT_DIRECT_MESSAGE = 1 << 12         # 私信
INTENT_C2C_GROUP_AT_MESSAGES = 1 << 25  # C2C/群@消息


class QQBotAdapter(BaseChannelAdapter):
    """轻量 QQ Bot 适配器，支持 token 刷新 + WebSocket 连接 + 消息收发。"""

    platform = "qqbot"

    def __init__(
        self,
        channel_id: str = "",
        app_id: str = "",
        client_secret: str = "",
        on_message: Any = None,
    ):
        super().__init__(channel_id, on_message)
        self._app_id = str(app_id)
        self._client_secret = str(client_secret)

        # Token
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0
        self._token_lock = asyncio.Lock()

        # WebSocket
        self._ws = None
        self._aiohttp_session = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._running = False
        self._session_id: Optional[str] = None
        self._seq: Optional[int] = None
        self._user_agent = "TaktonQQBot/1.0"

    # ─── Token ──────────────────────────────────────────────

    async def ensure_token(self) -> str:
        """获取/刷新 access_token"""
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token

        async with self._token_lock:
            if self._access_token and time.time() < self._token_expires_at - 60:
                return self._access_token

            import httpx
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    TOKEN_URL,
                    json={"appId": self._app_id, "clientSecret": self._client_secret},
                )
                resp.raise_for_status()
                data = resp.json()

            token = data.get("access_token")
            if not token:
                raise RuntimeError(f"QQ Bot token 响应缺少 access_token: {data}")

            expires_in = int(data.get("expires_in", 7200))
            self._access_token = token
            self._token_expires_at = time.time() + expires_in
            logger.info("QQ Bot access_token 刷新成功，有效期 %ds", expires_in)
            return self._access_token

    # ─── Gateway ────────────────────────────────────────────

    async def get_gateway_url(self) -> str:
        """获取 WebSocket Gateway URL"""
        token = await self.ensure_token()
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{API_BASE}{GATEWAY_URL_PATH}",
                headers={
                    "Authorization": f"QQBot {token}",
                    "User-Agent": self._user_agent,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        url = data.get("url")
        if not url:
            raise RuntimeError(f"QQ Bot gateway 响应缺少 url: {data}")
        return url

    # ─── 连接测试 ──────────────────────────────────────────

    async def test_connection(self) -> tuple[bool, str]:
        """测试连接：获取 token + gateway URL"""
        try:
            token = await self.ensure_token()
            gateway_url = await self.get_gateway_url()
            return True, f"连接成功，Gateway: {gateway_url[:50]}..."
        except Exception as e:
            return False, f"连接失败: {e}"

    # ─── WebSocket 连接 ────────────────────────────────────

    async def connect(self) -> None:
        """建立 WebSocket 连接并开始监听"""
        try:
            token = await self.ensure_token()
            gateway_url = await self.get_gateway_url()

            self._aiohttp_session = aiohttp.ClientSession()
            self._ws = await self._aiohttp_session.ws_connect(gateway_url)
            self._running = True

            # 等待 HELLO
            msg = await asyncio.wait_for(self._ws.receive_json(), timeout=20)
            if msg.get("op") != OP_HELLO:
                raise RuntimeError(f"期望 HELLO(10)，收到 op={msg.get('op')}")

            heartbeat_interval = msg["d"]["heartbeat_interval"]
            logger.info("QQ Bot HELLO，heartbeat_interval=%dms", heartbeat_interval)

            # IDENTIFY — 与 Hermes QQBot adapter 对齐
            intents = (
                (1 << 25)   # C2C_GROUP_AT_MESSAGES (C2C私信+群@)
                | (1 << 30) # PUBLIC_GUILD_MESSAGES (公域频道消息)
                | (1 << 12) # DIRECT_MESSAGE (频道私信)
                | (1 << 26) # INTERACTION (交互事件)
            )
            identify_payload = {
                "op": OP_IDENTIFY,
                "d": {
                    "token": f"QQBot {token}",
                    "intents": intents,
                    "shard": [0, 1],
                },
            }
            await self._ws.send_json(identify_payload)

            # 启动心跳和监听
            self._heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(heartbeat_interval / 1000)
            )
            self._listen_task = asyncio.create_task(self._listen_loop())

            self.connected = True
            logger.info("QQ Bot WebSocket 连接成功")

        except Exception as e:
            logger.error("QQ Bot 连接失败: %s", e)
            try:
                await self._cleanup()
            except Exception:
                pass  # cleanup 失败不二次抛
            raise

    async def _heartbeat_loop(self, interval: float):
        """心跳循环"""
        try:
            while self._running and self._ws and not self._ws.closed:
                await asyncio.sleep(interval)
                if self._running and self._ws and not self._ws.closed:
                    await self._ws.send_json({
                        "op": OP_HEARTBEAT,
                        "d": self._seq,
                    })
        except Exception as e:
            logger.error("QQ Bot heartbeat 错误: %s", e)
            if self._running:
                self._running = False

    async def _listen_loop(self):
        """消息监听循环"""
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = msg.json()
                    op = data.get("op")
                    t = data.get("t")
                    d = data.get("d")
                    s = data.get("s")

                    if s is not None:
                        self._seq = s

                    if op == OP_DISPATCH:
                        # READY 事件
                        if t == "READY":
                            self._session_id = d.get("session_id")
                            user = d.get("user", {})
                            logger.info(
                                "QQ Bot READY: %s, session_id=%s",
                                user.get("username", "unknown"),
                                self._session_id,
                            )
                        # 消息事件
                        elif t in (
                            "MESSAGE_CREATE",
                            "AT_MESSAGE_CREATE",
                            "DIRECT_MESSAGE_CREATE",
                            "C2C_MESSAGE_CREATE",
                            "GROUP_AT_MESSAGE_CREATE",
                        ):
                            logger.info(
                                "QQ Bot 收到消息: t=%s, content=%.100s",
                                t, (d.get("content") or "")[:100],
                            )
                            if self._on_message:
                                await self._on_message(t, d)
                        else:
                            logger.debug("QQ Bot dispatch: t=%s, d_keys=%s", t, list(d.keys()) if isinstance(d, dict) else type(d))

                    elif op == OP_RECONNECT:
                        logger.warning("QQ Bot 服务端要求重连")
                        self._running = False
                        break

                    elif op == OP_HEARTBEAT_ACK:
                        pass  # 正常

                elif msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    logger.warning("QQ Bot WebSocket 关闭")
                    break

        except Exception as e:
            logger.error("QQ Bot listen 错误: %s", e)
        finally:
            self._running = False
            self.connected = False

    # ─── 发送消息 ──────────────────────────────────────────

    async def _api_request(
        self, method: str, path: str, body: dict | None = None, timeout: float = 30.0
    ) -> dict:
        """发认证 REST API 请求到 QQ Bot API。"""
        token = await self.ensure_token()
        import httpx
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(
                method,
                f"{API_BASE}{path}",
                headers={
                    "Authorization": f"QQBot {token}",
                    "Content-Type": "application/json",
                    "User-Agent": self._user_agent,
                },
                json=body,
            )
            data = resp.json()
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"QQ Bot API error [{resp.status_code}] {path}: {data.get('message', data)}"
                )
            return data

    async def send_message(
        self,
        channel_id: str,
        content: str,
        msg_type: int = 0,
    ) -> dict:
        """发送消息到频道"""
        body = {"content": content[:4000], "msg_type": msg_type}
        return await self._api_request("POST", f"/channels/{channel_id}/messages", body)

    async def send_c2c_message(
        self, openid: str, content: str, msg_id: str = ""
    ) -> dict:
        """发送 C2C 私信消息（参考 Hermes _send_c2c_text）"""
        body: dict[str, Any] = {
            "content": content[:4000],
            "msg_type": 0,  # MSG_TYPE_TEXT
            "msg_seq": int(time.time() * 1000) % 1000000,
        }
        if msg_id:
            body["msg_id"] = msg_id
        return await self._api_request("POST", f"/v2/users/{openid}/messages", body)

    async def send_group_message(
        self, group_openid: str, content: str, msg_id: str = ""
    ) -> dict:
        """发送群消息（参考 Hermes _send_group_text）"""
        body: dict[str, Any] = {
            "content": content[:4000],
            "msg_type": 0,
            "msg_seq": int(time.time() * 1000) % 1000000,
        }
        if msg_id:
            body["msg_id"] = msg_id
        return await self._api_request("POST", f"/v2/groups/{group_openid}/messages", body)

    async def send_text(self, chat_id: str, text: str, **kwargs) -> dict:
        """通用发送文本消息（BaseChannelAdapter 接口实现）"""
        msg_type = kwargs.get("msg_type", "")
        if msg_type == "c2c":
            return await self.send_c2c_message(chat_id, text, msg_id=kwargs.get("msg_id", ""))
        elif msg_type == "group":
            return await self.send_group_message(chat_id, text, msg_id=kwargs.get("msg_id", ""))
        else:
            return await self.send_message(chat_id, text)

    # ─── 清理 ──────────────────────────────────────────────

    async def _cleanup(self):
        """关闭所有连接"""
        self._running = False
        self.connected = False

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None

        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None

        if self._aiohttp_session and not self._aiohttp_session.closed:
            await self._aiohttp_session.close()
        self._aiohttp_session = None

    async def disconnect(self):
        """断开连接"""
        await self._cleanup()
        logger.info("QQ Bot 已断开")