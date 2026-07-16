"""
xAI Grok OAuth（SuperGrok / X Premium+）设备码登录

流程对齐 Hermes Agent：
- 向 https://auth.x.ai 申请 device_code
- 用户在浏览器打开 verification_uri 并授权
- 后端轮询 token 端点，拿到 access_token + refresh_token
- 之后以 Bearer access_token 调用 https://api.x.ai/v1

注意：xAI 可能对 OAuth 推理有订阅档位限制；若 403 可改用 API Key 路径。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

DEFAULT_XAI_BASE_URL = "https://api.x.ai/v1"
XAI_OAUTH_ISSUER = "https://auth.x.ai"
XAI_OAUTH_DISCOVERY_URL = f"{XAI_OAUTH_ISSUER}/.well-known/openid-configuration"
# 与 Hermes 使用同一公开客户端（浏览器设备码，无 client_secret）
XAI_OAUTH_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_OAUTH_SCOPE = "openid profile email offline_access grok-cli:access api:access"
XAI_OAUTH_DEVICE_CODE_URL = f"{XAI_OAUTH_ISSUER}/oauth2/device/code"

# 内存中的进行中登录（device_code -> meta）
_pending: dict[str, dict[str, Any]] = {}


async def discover_token_endpoint(timeout: float = 15.0) -> str:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                XAI_OAUTH_DISCOVERY_URL,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    return f"{XAI_OAUTH_ISSUER}/oauth2/token"
                data = await resp.json(content_type=None)
                ep = str(data.get("token_endpoint") or "").strip()
                if ep and "x.ai" in ep:
                    return ep
    except Exception as e:
        logger.debug("xAI OIDC discovery failed: %s", e)
    return f"{XAI_OAUTH_ISSUER}/oauth2/token"


async def start_device_login() -> dict[str, Any]:
    """发起设备码登录，返回用户需打开的 URL 与 user_code。"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            XAI_OAUTH_DEVICE_CODE_URL,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={
                "client_id": XAI_OAUTH_CLIENT_ID,
                "scope": XAI_OAUTH_SCOPE,
            },
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            text = await resp.text()
            if resp.status != 200:
                return {
                    "ok": False,
                    "message": f"申请设备码失败 (HTTP {resp.status})",
                    "detail": text[:300],
                }
            try:
                payload = await resp.json(content_type=None)
            except Exception:
                return {"ok": False, "message": "设备码响应无法解析", "detail": text[:300]}

    required = (
        "device_code",
        "user_code",
        "verification_uri",
        "expires_in",
        "interval",
    )
    missing = [k for k in required if k not in payload]
    if missing:
        return {
            "ok": False,
            "message": f"设备码响应缺少字段: {', '.join(missing)}",
        }

    device_code = str(payload["device_code"])
    expires_in = int(payload.get("expires_in") or 900)
    interval = max(1, int(payload.get("interval") or 5))
    token_endpoint = await discover_token_endpoint()

    _pending[device_code] = {
        "created_at": time.time(),
        "expires_in": expires_in,
        "interval": interval,
        "token_endpoint": token_endpoint,
        "user_code": str(payload.get("user_code") or ""),
    }
    # 清理过期 pending
    now = time.time()
    for k, v in list(_pending.items()):
        if now - float(v.get("created_at") or 0) > float(v.get("expires_in") or 900) + 60:
            _pending.pop(k, None)

    return {
        "ok": True,
        "device_code": device_code,
        "user_code": str(payload.get("user_code") or ""),
        "verification_uri": str(payload.get("verification_uri") or "https://accounts.x.ai"),
        "verification_uri_complete": str(
            payload.get("verification_uri_complete")
            or payload.get("verification_uri")
            or ""
        ),
        "expires_in": expires_in,
        "interval": interval,
        "message": "请在浏览器打开验证链接并输入代码完成授权",
    }


async def poll_device_login(device_code: str) -> dict[str, Any]:
    """轮询一次设备码授权结果。"""
    meta = _pending.get(device_code)
    if not meta:
        return {
            "ok": False,
            "status": "expired",
            "message": "登录会话已失效，请重新发起 OAuth",
        }

    created = float(meta.get("created_at") or 0)
    expires_in = int(meta.get("expires_in") or 900)
    if time.time() - created > expires_in:
        _pending.pop(device_code, None)
        return {
            "ok": False,
            "status": "expired",
            "message": "授权超时，请重新点击登录",
        }

    token_endpoint = str(meta.get("token_endpoint") or "")
    async with aiohttp.ClientSession() as session:
        async with session.post(
            token_endpoint,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": XAI_OAUTH_CLIENT_ID,
                "device_code": device_code,
            },
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            text = await resp.text()
            try:
                payload = await resp.json(content_type=None) if text else {}
            except Exception:
                payload = {}

            if resp.status == 200 and payload.get("access_token"):
                _pending.pop(device_code, None)
                access = str(payload["access_token"])
                refresh = str(payload.get("refresh_token") or "")
                expires_in_tok = int(payload.get("expires_in") or 3600)
                expires_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=max(60, expires_in_tok - 60))
                ).isoformat()
                return {
                    "ok": True,
                    "status": "authorized",
                    "access_token": access,
                    "refresh_token": refresh,
                    "expires_at": expires_at,
                    "expires_in": expires_in_tok,
                    "base_url": DEFAULT_XAI_BASE_URL,
                    "message": "Grok OAuth 登录成功",
                }

            error_code = str(payload.get("error") or "")
            if error_code == "authorization_pending":
                return {
                    "ok": False,
                    "status": "pending",
                    "message": "等待浏览器中完成授权…",
                    "interval": int(meta.get("interval") or 5),
                }
            if error_code == "slow_down":
                return {
                    "ok": False,
                    "status": "pending",
                    "message": "请稍候…",
                    "interval": min(int(meta.get("interval") or 5) + 2, 15),
                }
            if error_code == "expired_token":
                _pending.pop(device_code, None)
                return {
                    "ok": False,
                    "status": "expired",
                    "message": "设备码已过期，请重新登录",
                }
            if error_code == "access_denied":
                _pending.pop(device_code, None)
                return {
                    "ok": False,
                    "status": "denied",
                    "message": "用户拒绝了授权",
                }

            if resp.status >= 400:
                desc = (
                    payload.get("error_description")
                    or payload.get("error")
                    or text[:200]
                )
                return {
                    "ok": False,
                    "status": "error",
                    "message": f"轮询失败: {desc}",
                }

    return {
        "ok": False,
        "status": "pending",
        "message": "等待授权…",
        "interval": int(meta.get("interval") or 5),
    }


async def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """用 refresh_token 刷新 access_token。"""
    if not refresh_token or not str(refresh_token).strip():
        return {"ok": False, "message": "缺少 refresh_token，请重新 OAuth 登录"}
    token_endpoint = await discover_token_endpoint()
    async with aiohttp.ClientSession() as session:
        async with session.post(
            token_endpoint,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
            data={
                "grant_type": "refresh_token",
                "client_id": XAI_OAUTH_CLIENT_ID,
                "refresh_token": refresh_token.strip(),
            },
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            text = await resp.text()
            try:
                payload = await resp.json(content_type=None) if text else {}
            except Exception:
                payload = {}
            if resp.status != 200 or not payload.get("access_token"):
                return {
                    "ok": False,
                    "message": payload.get("error_description")
                    or payload.get("error")
                    or f"刷新失败 HTTP {resp.status}",
                    "status_code": resp.status,
                    "detail": text[:300],
                }
            expires_in_tok = int(payload.get("expires_in") or 3600)
            expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=max(60, expires_in_tok - 60))
            ).isoformat()
            return {
                "ok": True,
                "access_token": str(payload["access_token"]),
                "refresh_token": str(payload.get("refresh_token") or refresh_token),
                "expires_at": expires_at,
                "expires_in": expires_in_tok,
            }


def token_needs_refresh(expires_at: str | None, skew_seconds: int = 120) -> bool:
    if not expires_at:
        return False
    try:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= exp - timedelta(seconds=skew_seconds)
    except Exception:
        return False
