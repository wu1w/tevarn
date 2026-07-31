"""CapabilityToken 的 HMAC 签名（审计缺口 #5：序列化 Token 防伪造）。

场景：Token 序列化后跨边界传输（未来多设备/跨实例同步、
工作流暂停恢复、外部存储）。无签名的 Token 反序列化时无法区分
「Kernel 签发」与「调用方伪造」——伪造 `capabilities=["*"]` 即提权。

H2-C1 密钥优先级：
1. ``TAKTON_TOKEN_HMAC_SECRET`` / ``settings.agent_token_hmac_secret``（推荐，与 JWT 解耦）
2. 否则从 ``jwt_secret`` 经 HKDF 派生（兼容旧部署；泄露 JWT 仍可伪造 token）
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_HMAC_INFO = b"takton-kernel-token-hmac-v1"
_key_cache: bytes | None = None
_key_source: str = "unset"


class TokenSignatureError(PermissionError):
    """Token 签名缺失或验证失败——按伪造处理，一律拒绝。"""


def hmac_key_source() -> str:
    """env | settings | derived_jwt — for health/dashboard."""
    _hmac_key()
    return _key_source


def _hmac_key() -> bytes:
    global _key_cache, _key_source
    if _key_cache is not None:
        return _key_cache
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    # 1) dedicated secret (production preferred)
    dedicated = (os.environ.get("TAKTON_TOKEN_HMAC_SECRET") or "").strip()
    if not dedicated:
        try:
            from backend.core.config import settings

            dedicated = str(
                getattr(settings, "agent_token_hmac_secret", "") or ""
            ).strip()
        except Exception:
            dedicated = ""
    if len(dedicated) >= 16:
        _key_cache = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=_HMAC_INFO + b"|dedicated",
        ).derive(dedicated.encode("utf-8"))
        _key_source = "dedicated"
        return _key_cache

    # 2) legacy: derive from jwt_secret
    from backend.core.config import settings

    _key_cache = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_HMAC_INFO,
    ).derive(settings.jwt_secret.encode("utf-8"))
    _key_source = "derived_jwt"
    logger.warning(
        "H2: token HMAC derived from jwt_secret; set TAKTON_TOKEN_HMAC_SECRET "
        "to decouple JWT and capability signing"
    )
    return _key_cache


def _canonical_payload(data: dict[str, Any]) -> bytes:
    """签名覆盖的语义字段（不含 signature 自身）。"""
    payload = {
        "id": data.get("id"),
        "process_id": data.get("process_id"),
        "parent_token_id": data.get("parent_token_id"),
        "capabilities": sorted(data.get("capabilities") or []),
        "issued_at": data.get("issued_at"),
        "expires_at": data.get("expires_at"),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")


def sign_token_dict(data: dict[str, Any]) -> str:
    """计算 Token 字典的 HMAC 签名（hex）。"""
    return hmac.new(_hmac_key(), _canonical_payload(data), hashlib.sha256).hexdigest()


def verify_token_dict(data: dict[str, Any]) -> bool:
    """验证签名。签名缺失或不匹配返回 False（调用方决定拒绝策略）。"""
    sig = data.get("signature")
    if not isinstance(sig, str) or not sig:
        return False
    expected = sign_token_dict(data)
    return hmac.compare_digest(sig, expected)
