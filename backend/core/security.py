"""
Security utilities
JWT token creation/decoding and password hashing
"""

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt  # PyJWT（2026-07 替换弃维护的 python-jose，连带消除 ecdsa/pyasn1 CVE）
from jwt import PyJWTError as JWTError
from passlib.context import CryptContext

from backend.core.config import settings

# Password hashing
# bcrypt 4.1+ 移除了 __about__，passlib 会打 warning；输入超过 72 bytes 时显式拒绝。
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _truncate_password(password: str) -> str:
    """bcrypt 最多 72 bytes；禁止静默截断造成不同密码等价。"""
    raw = password.encode("utf-8")
    if len(raw) <= 72:
        return password
    raise ValueError("password must be at most 72 UTF-8 bytes")


def _assert_password_backend_usable() -> None:
    """启动自检：passlib + bcrypt 的组合能不能正常工作。

    passlib 1.7.4 是 2020 年的最后一版、已停止维护，它通过 `bcrypt.__about__`
    读版本号 —— 而 bcrypt 4.1 起移除了这个属性。组合坏掉时的表现极具迷惑性：
    **任何**密码（哪怕 5 个字符）都会抛
    `ValueError: password cannot be longer than 72 bytes`，
    于是登录、注册、单用户初始化全部 500，而错误信息把人往「密码太长」上带。

    对一个用户自己装依赖的开源项目，一次 `pip install -U bcrypt` 就会踩中。
    与其等到用户登录时才炸，不如启动时说清楚。
    """
    try:
        pwd_context.hash("takton-startup-selfcheck")
    except Exception as e:
        try:
            import bcrypt as _b

            ver = getattr(_b, "__version__", "unknown")
        except Exception:
            ver = "not installed"
        raise RuntimeError(
            f"Password hashing backend is broken (bcrypt=={ver}): {e}\n"
            f"passlib 1.7.4 is unmaintained and incompatible with bcrypt >= 4.1 "
            f"(it reads the removed `bcrypt.__about__`). Symptom: every password, "
            f"however short, fails with 'password cannot be longer than 72 bytes'.\n"
            f"Fix:  pip install 'bcrypt>=4.0.1,<4.1'"
        ) from e

# JWT configuration
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7


def password_token_stamp(hashed_password: str | None) -> str:
    """密码哈希指纹：改密后旧 JWT 的 pwc 不匹配即失效（无需 DB 迁移）。"""
    raw = (hashed_password or "").encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()[:16]


def create_access_token(
    data: dict[str, Any],
    *,
    hashed_password: str | None = None,
) -> str:
    """Create a JWT access token.

    若传入 hashed_password，会写入 ``pwc`` 声明；改密后旧 token 在鉴权时被拒。
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    if hashed_password is not None:
        to_encode["pwc"] = password_token_stamp(hashed_password)
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret, algorithm=ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Decode and verify a JWT access token"""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGORITHM],
            options={"require": ["exp"]}
        )
        return payload
    except JWTError:
        return None


def token_password_matches(payload: dict[str, Any] | None, hashed_password: str | None) -> bool:
    """校验 JWT pwc 与当前密码哈希是否一致。

    - 无 pwc（旧 token）：仍放行一次兼容，但建议重新登录拿带 pwc 的 token
    - 有 pwc 且不匹配：改密后旧 token → 拒绝
    """
    if not payload:
        return False
    stamp = payload.get("pwc")
    if not stamp:
        return True  # legacy tokens without pwc
    return str(stamp) == password_token_stamp(hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password"""
    return pwd_context.hash(_truncate_password(password))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash"""
    return pwd_context.verify(_truncate_password(plain_password), hashed_password)
