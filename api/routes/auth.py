"""
认证路由
用户注册、登录、登出、获取当前用户信息
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError

from backend.core.config import settings
from backend.core.security import (
    create_access_token,
    decode_access_token,
    get_password_hash,
    verify_password,
)
from backend.core.unit_of_work import UnitOfWork
from backend.repositories import UserRepository
from backend.schemas import PasswordChange, TokenResponse, UserLogin, UserRead, UserRegister, UserUpdate

from ..dependencies import get_current_user, get_user_repo

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/register", response_model=TokenResponse)
async def register(
    data: UserRegister,
    repo: UserRepository = Depends(get_user_repo),
):
    """用户注册（唯一性检查与创建在同一事务）"""
    async with UnitOfWork() as uow:
        # 检查邮箱是否已存在
        existing = await uow.users.get_by_email(str(data.email))
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered",
            )

        # 检查用户名是否已存在
        existing = await uow.users.get_by_username(data.username)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already taken",
            )

        # 创建用户
        user_data = data.model_dump()
        user_data["hashed_password"] = get_password_hash(user_data.pop("password"))
        # 安全修复：默认非管理员，仅第一个用户自动设为管理员
        user_data.setdefault("is_superuser", False)
        # 检查是否已有用户，若无则设为管理员（初始化场景）
        existing_count = await uow.users.count()
        if existing_count == 0:
            user_data["is_superuser"] = True
        try:
            user = await uow.users.create(user_data)
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email or username already exists",
            ) from exc

        # 生成令牌（在 uow 事务内，确保 user 已定义且事务未关闭）
        token = create_access_token({"sub": str(user.id)})

    return TokenResponse(
        access_token=token,
        user=UserRead.model_validate(user),
    )


@router.post("/auto-login", response_model=TokenResponse)
async def auto_login(
    repo: UserRepository = Depends(get_user_repo),
):
    """单用户模式：自动登录/创建默认用户"""
    if not settings.single_user_mode:
        raise HTTPException(status_code=403, detail="Single user mode is disabled")

    # 查找或创建默认用户
    async with UnitOfWork() as uow:
        existing = await uow.users.get_by_email("admin@takton.dev")
        if existing:
            user = existing
        else:
            import os
            default_pw = (
                (settings.default_admin_password or "").strip()
                or os.environ.get("TAKTON_DEFAULT_ADMIN_PASSWORD", "").strip()
                or "admin"
            )
            user_data = {
                "email": "admin@takton.dev",
                "username": "admin",
                "hashed_password": get_password_hash(default_pw),
                "is_superuser": True,
                "is_active": True,
            }
            user = await uow.users.create(user_data)

    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(
        access_token=token,
        user=UserRead.model_validate(user),
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    data: UserLogin,
    repo: UserRepository = Depends(get_user_repo),
):
    """用户登录（校验与 last_login 更新在同一事务）"""
    async with UnitOfWork() as uow:
        user = await uow.users.get_by_email(str(data.email))
        if not user or not verify_password(data.password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is deactivated",
            )

        # 更新最后登录时间
        await uow.users.update_last_login(user.id)

    # 生成令牌
    token = create_access_token({"sub": str(user.id)})

    return TokenResponse(
        access_token=token,
        user=UserRead.model_validate(user),
    )


@router.get("/me", response_model=UserRead)
async def get_me(
    current_user: UserRead = Depends(get_current_user),
):
    """获取当前登录用户信息"""
    return current_user


@router.patch("/me", response_model=UserRead)
async def update_me(
    data: UserUpdate,
    current_user: UserRead = Depends(get_current_user),
    repo: UserRepository = Depends(get_user_repo),
):
    """更新当前用户信息"""
    updated = await repo.update(current_user.id, data.model_dump(exclude_unset=True))
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return UserRead.model_validate(updated)


@router.post("/me/password")
async def change_password(
    data: PasswordChange,
    current_user: UserRead = Depends(get_current_user),
    repo: UserRepository = Depends(get_user_repo),
):
    """修改当前用户密码"""
    user = await repo.get_by_id(current_user.id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    if not verify_password(data.old_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Old password is incorrect",
        )

    await repo.update(
        current_user.id,
        {"hashed_password": get_password_hash(data.new_password)},
    )
    return {"ok": True, "message": "Password changed successfully"}
