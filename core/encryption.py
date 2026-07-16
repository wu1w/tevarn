"""
Setting 敏感字段加密工具
基于 Fernet (AES-128-CBC + HMAC) 对存储在数据库中的 API Key 等敏感值进行加密。
"""

import logging
import os
from base64 import urlsafe_b64encode
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from backend.core.config import settings

logger = logging.getLogger(__name__)


def _derive_key_from_jwt_secret() -> bytes:
    """当未配置独立加密密钥时，使用 HKDF 从 JWT_SECRET 派生 32 字节密钥。

    salt 必须跨进程稳定，否则重启后无法解密已加密的 settings。
    优先：settings.settings_encryption_salt → SETTINGS_ENCRYPTION_SALT →
    派生自 jwt_secret 的固定 HKDF 材料（最后手段，仍保证确定性）。
    """
    salt_str = (
        (settings.settings_encryption_salt or "").strip()
        or os.environ.get("SETTINGS_ENCRYPTION_SALT", "").strip()
        or os.environ.get("TAKTON_SETTINGS_ENCRYPTION_SALT", "").strip()
    )
    if salt_str:
        salt = salt_str.encode("utf-8")
    else:
        # 确定性 fallback：不使用随机 salt，避免每次重启密钥变化
        salt = HKDF(
            algorithm=hashes.SHA256(),
            length=16,
            salt=b"takton-fallback-salt-v1",
            info=b"takton-settings-salt-fallback",
        ).derive(settings.jwt_secret.encode("utf-8"))
        logger.warning(
            "SETTINGS_ENCRYPTION_SALT is not set; using deterministic fallback salt "
            "derived from JWT secret. Set TAKTON_SETTINGS_ENCRYPTION_SALT for isolation."
        )
    return urlsafe_b64encode(
        HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            info=b"takton-settings-encryption-v1",
        ).derive(settings.jwt_secret.encode("utf-8"))
    )


_fernet_cache: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet_cache
    if _fernet_cache is not None:
        return _fernet_cache

    raw_key = os.environ.get("SETTINGS_ENCRYPTION_KEY", "").strip()
    if not raw_key:
        logger.warning(
            "SETTINGS_ENCRYPTION_KEY is not set; deriving encryption key from JWT_SECRET. "
            "Set a dedicated SETTINGS_ENCRYPTION_KEY for stronger isolation."
        )
        _fernet_cache = Fernet(_derive_key_from_jwt_secret())
    elif len(raw_key) == 44:  # 标准 Fernet key 长度
        _fernet_cache = Fernet(raw_key)
    else:
        # 安全修复：使用与 _derive_key_from_jwt_secret 一致的 salt，不再硬编码
        _fernet_cache = Fernet(_derive_key_from_jwt_secret())

    return _fernet_cache


def _is_sensitive_key(key: str) -> bool:
    return isinstance(key, str) and key.endswith("_api_key")


def encrypt_setting(value: Any) -> Any:
    """对敏感值进行加密；非字符串/空值原样返回。"""
    if not isinstance(value, str) or not value:
        return value
    try:
        return _get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")
    except Exception as e:
        logger.error(f"Failed to encrypt setting value: {e}")
        raise


def decrypt_setting(value: Any) -> Any:
    """对加密值进行解密；非字符串/空值原样返回。"""
    if not isinstance(value, str) or not value:
        return value
    try:
        return _get_fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        # 可能是旧明文数据，直接返回原值并在日志提示
        logger.debug("Setting value is not a valid Fernet token, returning as-is (legacy plaintext).")
        return value
    except Exception as e:
        logger.error(f"Failed to decrypt setting value: {e}")
        raise


def mask_setting(key: str, value: Any) -> Any:
    """对 *_api_key 类字段返回脱敏值，其他字段原样返回。"""
    if not _is_sensitive_key(key):
        return value
    if not isinstance(value, str) or not value:
        return value
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"
