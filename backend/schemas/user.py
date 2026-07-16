"""
User 相关 Pydantic Schema
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class UserBase(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=64)
    display_name: Optional[str] = Field(None, max_length=128)


class UserRegister(UserBase):
    """用户注册请求"""

    password: str = Field(..., min_length=8, max_length=128)


class UserLogin(BaseModel):
    """用户登录请求"""

    email: EmailStr
    password: str


class UserUpdate(BaseModel):
    """用户信息更新请求"""

    display_name: Optional[str] = Field(None, max_length=128)
    avatar_url: Optional[str] = None


class UserRead(UserBase):
    """用户信息响应（序列化时不含密码哈希）"""

    id: uuid.UUID
    is_active: bool
    is_superuser: bool
    last_login_at: Optional[datetime]
    created_at: datetime
    # 保留密码哈希供内部认证逻辑使用，但序列化时排除，避免泄露
    hashed_password: str = Field(..., exclude=True)

    model_config = {"from_attributes": True}


class PasswordChange(BaseModel):
    """修改密码请求"""

    old_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


class TokenResponse(BaseModel):
    """登录令牌响应"""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = 604800  # 7 days in seconds
    user: UserRead
