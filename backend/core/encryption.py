"""
Setting 敏感字段加密工具
基于 Fernet (AES-128-CBC + HMAC) 对存储在数据库中的 API Key 等敏感值进行加密。

审计 P0-C2：默认使用独立密钥（~/.tevarn/secrets.json），不再仅由 JWT 派生。
JWT 派生密钥仅作解密回落，兼容历史数据。
"""

import json
import logging
import os
from base64 import urlsafe_b64encode
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from backend.core.config import settings

logger = logging.getLogger(__name__)


def _secrets_file_path() -> Path:
    override = os.environ.get("TEVARN_SECRETS_FILE", "").strip()
    if override:
        return Path(override)
    try:
        from backend.core.config import get_tevarn_home
        return get_tevarn_home() / "secrets.json"
    except Exception:
        return Path.home() / ".tevarn" / "secrets.json"


def _derive_key_from_jwt_secret() -> bytes:
    """历史兼容：从 JWT_SECRET 派生 Fernet 密钥（仅解密回落，不再作为默认写密钥）。"""
    salt_str = (
        (settings.settings_encryption_salt or "").strip()
        or os.environ.get("SETTINGS_ENCRYPTION_SALT", "").strip()
        or os.environ.get("TEVARN_SETTINGS_ENCRYPTION_SALT", "").strip()
    )
    if salt_str:
        salt = salt_str.encode("utf-8")
    else:
        salt = HKDF(
            algorithm=hashes.SHA256(),
            length=16,
            salt=b"tevarn-fallback-salt-v1",
            info=b"tevarn-settings-salt-fallback",
        ).derive(settings.jwt_secret.encode("utf-8"))
    return urlsafe_b64encode(
        HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            info=b"tevarn-settings-encryption-v1",
        ).derive(settings.jwt_secret.encode("utf-8"))
    )


def _load_or_create_independent_key() -> str:
    """独立加密密钥：env → secrets.json → 生成并持久化。"""
    raw = (
        os.environ.get("SETTINGS_ENCRYPTION_KEY", "").strip()
        or os.environ.get("TEVARN_SETTINGS_ENCRYPTION_KEY", "").strip()
    )
    if raw:
        # 校验可构造 Fernet
        Fernet(raw.encode("utf-8") if not isinstance(raw, (bytes, bytearray)) else raw)
        return raw if isinstance(raw, str) else raw.decode("utf-8")

    path = _secrets_file_path()
    data: dict = {}
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}

    stored = str(data.get("settings_encryption_key", "")).strip()
    if stored:
        try:
            Fernet(stored.encode("utf-8"))
            os.environ.setdefault("SETTINGS_ENCRYPTION_KEY", stored)
            return stored
        except Exception:
            logger.warning("Stored settings_encryption_key invalid; regenerating")

    new_key = Fernet.generate_key().decode("utf-8")
    data["settings_encryption_key"] = new_key
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        logger.info(
            "Generated independent SETTINGS_ENCRYPTION_KEY and persisted to %s",
            path,
        )
    except Exception as e:
        logger.warning(
            "Cannot persist SETTINGS_ENCRYPTION_KEY to %s (%s); using ephemeral key",
            path,
            e,
        )
    os.environ["SETTINGS_ENCRYPTION_KEY"] = new_key
    return new_key


_fernet_cache: Fernet | None = None
_legacy_fernet_cache: Fernet | None = None


def _get_fernet() -> Fernet:
    """写路径 / 主读路径：独立密钥（与 JWT 解耦）。"""
    global _fernet_cache
    if _fernet_cache is not None:
        return _fernet_cache

    try:
        raw_key = _load_or_create_independent_key()
        _fernet_cache = Fernet(raw_key.encode("utf-8"))
    except Exception as e:
        logger.error(
            "Independent SETTINGS_ENCRYPTION_KEY unavailable (%s); "
            "falling back to JWT-derived key for this process",
            e,
        )
        _fernet_cache = Fernet(_derive_key_from_jwt_secret())

    return _fernet_cache


def _get_legacy_fernet() -> Fernet:
    """解密回落：历史 JWT 派生密钥。"""
    global _legacy_fernet_cache
    if _legacy_fernet_cache is None:
        _legacy_fernet_cache = Fernet(_derive_key_from_jwt_secret())
    return _legacy_fernet_cache


def _is_sensitive_key(key: str) -> bool:
    return isinstance(key, str) and key.endswith("_api_key")


def encrypt_setting(value: Any, key: str | None = None) -> Any:
    """仅对敏感字段加密；非字符串/空值原样返回。

    历史版本曾加密全部字符串字段；现仅 *_api_key 落库加密。
    传 key 时可避免把 URL/模型名等明文误加密。
    """
    if key is not None and not _is_sensitive_key(key):
        return value
    if not isinstance(value, str) or not value:
        return value
    # 已是 Fernet token 则不重复加密
    if value.startswith("gAAAAA"):
        return value
    try:
        return _get_fernet().encrypt(value.encode("utf-8")).decode("utf-8")
    except Exception as e:
        logger.error(f"Failed to encrypt setting value: {e}")
        raise


def decrypt_setting(value: Any, key: str | None = None) -> Any:
    """解密敏感值；兼容历史「全字段加密」数据。

    - 非字符串/空：原样
    - 不像 Fernet token：原样（明文）
    - 解密失败：敏感字段返回空串（避免泄漏密文碎片）；非敏感字段返回空串并打日志
      （避免前端预填 gAAAAA...）
    """
    if not isinstance(value, str) or not value:
        return value
    # Fernet token 典型前缀
    if not value.startswith("gAAAAA"):
        return value
    try:
        return _get_fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        # 历史数据：JWT 派生密钥解密回落（审计 P0-C2 迁移兼容）
        try:
            return _get_legacy_fernet().decrypt(value.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            pass
        if key is not None and not _is_sensitive_key(key):
            logger.warning(
                "Setting %s looks encrypted but cannot decrypt (key mismatch?). "
                "Returning empty to avoid showing ciphertext in UI.",
                key,
            )
            return ""
        logger.debug("Setting value is not decryptable with current key, key=%s", key)
        if key is not None and _is_sensitive_key(key):
            return ""
        return value
    except Exception as e:
        logger.error(f"Failed to decrypt setting value: {e}")
        raise


def mask_setting(key: str, value: Any) -> Any:
    """对 *_api_key 类字段返回脱敏值，其他字段原样返回。"""
    if not _is_sensitive_key(key):
        # 防御：非敏感字段若仍是密文形态，不暴露给前端
        if isinstance(value, str) and value.startswith("gAAAAA"):
            return ""
        return value
    if not isinstance(value, str) or not value:
        return value
    if value.startswith("gAAAAA"):
        return "***"
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"
