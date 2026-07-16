"""
企业微信适配器

参考 Hermes WeCom adapter：
- 获取 access_token（corp_id + corp_secret）
- 接收消息：内嵌 Webhook 服务接收回调（需公网或内网穿透）
- 发送消息：REST API
- 配置：corp_id, corp_secret, agent_id, callback_token, callback_aes_key

消息回调流程：
1. 企业微信管理后台配置回调 URL → http://your-host:port/api/channel/wecom/callback
2. 企业微信验证 URL（GET 请求，需解密 echostr）
3. 收到消息（POST 请求，需解密 XML）
4. 解析后调用 on_message 回调
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import struct
import time
import xml.etree.ElementTree as ET
from typing import Any

from .base import BaseChannelAdapter

logger = logging.getLogger(__name__)

API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"


def _pkcs7_decode(data: bytes) -> bytes:
    """PKCS#7 去填充"""
    pad = data[-1]
    if pad < 1 or pad > 32:
        return data
    return data[:-pad]


class WeComCrypto:
    """企业微信消息加解密（AES-256-CBC）"""

    def __init__(self, token: str, aes_key: str, corp_id: str):
        self._token = token
        self._corp_id = corp_id
        # AES key: EncodingAESKey 是 43 字符，Base64 解码得 32 字节
        import base64
        self._aes_key = base64.b64decode(aes_key + "=")
        self._iv = self._aes_key[:16]

    def verify_signature(self, signature: str, timestamp: str, nonce: str, encrypt: str) -> bool:
        """验证消息签名"""
        items = sorted([self._token, timestamp, nonce, encrypt])
        sha1 = hashlib.sha1("".join(items).encode()).hexdigest()
        return sha1 == signature

    def decrypt(self, encrypt: str) -> tuple[str, str]:
        """解密消息，返回 (xml_content, from_corp_id)"""
        from Crypto.Cipher import AES
        cipher = AES.new(self._aes_key, AES.MODE_CBC, self._iv)
        plain = cipher.decrypt(bytes.fromhex(encrypt.encode().hex()))
        plain = _pkcs7_decode(plain)
        # 格式: random(16) + msg_len(4) + msg + corp_id
        msg_len = struct.unpack("!I", plain[16:20])[0]
        msg = plain[20:20 + msg_len].decode("utf-8")
        corp_id = plain[20 + msg_len:].decode("utf-8")
        return msg, corp_id

    def encrypt(self, reply_msg: str) -> str:
        """加密回复消息"""
        import base64
        from Crypto.Cipher import AES
        import os
        random_bytes = os.urandom(16)
        msg_bytes = reply_msg.encode("utf-8")
        corp_bytes = self._corp_id.encode("utf-8")
        msg_len = struct.pack("!I", len(msg_bytes))
        # PKCS#7 填充
        raw = random_bytes + msg_len + msg_bytes + corp_bytes
        pad_len = 32 - (len(raw) % 32)
        raw += bytes([pad_len] * pad_len)
        cipher = AES.new(self._aes_key, AES.MODE_CBC, self._iv)
        encrypted = cipher.encrypt(raw)
        return base64.b64encode(encrypted).decode("utf-8")

    def generate_signature(self, timestamp: str, nonce: str, encrypt: str) -> str:
        items = sorted([self._token, timestamp, nonce, encrypt])
        return hashlib.sha1("".join(items).encode()).hexdigest()


class WeComAdapter(BaseChannelAdapter):
    """企业微信适配器（回调 Webhook 模式）"""

    platform = "wecom"

    def __init__(
        self,
        channel_id: str,
        corp_id: str,
        corp_secret: str,
        agent_id: str = "",
        callback_token: str = "",
        callback_aes_key: str = "",
        on_message: Any = None,
    ):
        super().__init__(channel_id, on_message)
        self._corp_id = corp_id
        self._corp_secret = corp_secret
        self._agent_id = agent_id
        self._callback_token = callback_token
        self._callback_aes_key = callback_aes_key
        self._access_token = None
        self._token_expires_at = 0
        self._token_lock = asyncio.Lock()
        self._crypto = None
        if callback_token and callback_aes_key:
            try:
                self._crypto = WeComCrypto(callback_token, callback_aes_key, corp_id)
            except Exception as e:
                logger.warning(f"WeCom crypto init failed: {e}")

    async def _ensure_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token
        async with self._token_lock:
            import httpx
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    f"{API_BASE}/gettoken",
                    params={"corpid": self._corp_id, "corpsecret": self._corp_secret},
                )
                data = resp.json()
            if data.get("errcode", 0) != 0:
                raise RuntimeError(f"WeCom token 错误: {data.get('errmsg', '')}")
            self._access_token = data["access_token"]
            self._token_expires_at = time.time() + int(data.get("expires_in", 7200))
            logger.info("WeCom access_token 刷新成功")
            return self._access_token

    async def connect(self) -> None:
        """连接：获取 token 并注册回调路由"""
        await self._ensure_token()
        self.connected = True
        # 注册回调路由到 FastAPI app
        await self._register_callback_route()
        logger.info("WeCom 连接成功 (corp_id=%s, agent_id=%s)", self._corp_id, self._agent_id)

    async def _register_callback_route(self):
        """在 FastAPI app 上注册 /api/channel/wecom/callback 路由"""
        try:
            from backend.main import app
            from fastapi import Request, Response

            adapter = self

            @app.get("/api/channel/wecom/callback")
            async def wecom_verify(request: Request):
                """企业微信验证 URL（GET）"""
                msg_signature = request.query_params.get("msg_signature", "")
                timestamp = request.query_params.get("timestamp", "")
                nonce = request.query_params.get("nonce", "")
                echostr = request.query_params.get("echostr", "")

                if not adapter._crypto:
                    return Response(content="crypto not configured", status_code=500)

                if not adapter._crypto.verify_signature(msg_signature, timestamp, nonce, echostr):
                    return Response(content="signature mismatch", status_code=403)

                try:
                    reply, _ = adapter._crypto.decrypt(echostr)
                    return Response(content=reply)
                except Exception as e:
                    logger.error(f"WeCom verify decrypt failed: {e}")
                    return Response(content="decrypt failed", status_code=500)

            @app.post("/api/channel/wecom/callback")
            async def wecom_callback(request: Request):
                """企业微信消息回调（POST）"""
                body = await request.body()
                try:
                    root = ET.fromstring(body)
                    encrypt = root.findtext("Encrypt", "")
                except ET.ParseError:
                    return Response(content="<xml></xml>")

                msg_signature = request.query_params.get("msg_signature", "")
                timestamp = request.query_params.get("timestamp", "")
                nonce = request.query_params.get("nonce", "")

                if adapter._crypto:
                    if not adapter._crypto.verify_signature(msg_signature, timestamp, nonce, encrypt):
                        return Response(content="<xml></xml>")
                    try:
                        xml_content, _ = adapter._crypto.decrypt(encrypt)
                        msg_root = ET.fromstring(xml_content)
                    except Exception as e:
                        logger.error(f"WeCom decrypt failed: {e}")
                        return Response(content="<xml></xml>")
                else:
                    msg_root = root

                # 解析消息
                msg_type = msg_root.findtext("MsgType", "")
                from_user = msg_root.findtext("FromUserName", "")
                content = msg_root.findtext("Content", "")
                to_user = msg_root.findtext("ToUserName", "")

                if msg_type == "text" and content and from_user:
                    logger.info(f"WeCom 收到消息: from={from_user}, content={content[:50]}")
                    if adapter._on_message:
                        await adapter._on_message("wecom.message", {
                            "_platform": "wecom",
                            "event_type": "TEXT_MESSAGE",
                            "from_user": from_user,
                            "to_user": to_user,
                            "content": content,
                            "agent_id": adapter._agent_id,
                        })

                return Response(content="<xml></xml>")

            logger.info("WeCom callback route registered: /api/channel/wecom/callback")
        except Exception as e:
            logger.warning(f"WeCom callback route registration failed: {e}")

    async def disconnect(self) -> None:
        self.connected = False

    async def send_text(self, chat_id: str, text: str, **kwargs) -> dict:
        """发送文本消息"""
        token = await self._ensure_token()
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{API_BASE}/message/send",
                params={"access_token": token},
                json={
                    "touser": chat_id,
                    "msgtype": "text",
                    "agentid": int(self._agent_id) if self._agent_id else 0,
                    "text": {"content": text[:2048]},
                },
            )
            data = resp.json()
            if data.get("errcode", 0) != 0:
                raise RuntimeError(f"WeCom send 错误: {data.get('errmsg', '')}")
            return data

    async def test_connection(self) -> tuple[bool, str]:
        try:
            await self._ensure_token()
            return True, f"✅ 连接成功 (corp_id={self._corp_id[:8]}...)"
        except Exception as e:
            return False, f"❌ 连接失败: {e}"
