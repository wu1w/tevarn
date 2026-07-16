"""
健康检查路由
"""

from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("")
async def health_check():
    """服务健康检查"""
    return {"status": "ok", "service": "takton-backend"}


# 兼容旧路由：不带前缀的版本
_health_router = APIRouter(tags=["Health"])


@_health_router.get("/health")
async def health_check_root():
    """服务健康检查（根路径兼容）"""
    return {"status": "ok", "service": "takton-backend"}
