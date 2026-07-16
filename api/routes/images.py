"""
图片生成路由
提供图片生成 API
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.schemas.user import UserRead

from ..dependencies import get_current_user
from backend.services.image.factory import ImageGenerationServiceFactory

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/images", tags=["Images"])


class ImageGenerateRequest(BaseModel):
    """图片生成请求"""

    prompt: str = Field(..., min_length=1, max_length=4000)
    width: int = Field(1024, ge=64, le=2048)
    height: int = Field(1024, ge=64, le=2048)
    model: str | None = None
    n: int = Field(1, ge=1, le=4)


class ImageGenerateResponse(BaseModel):
    """图片生成响应"""

    images: list[dict[str, Any]]


@router.post("/generate", response_model=ImageGenerateResponse)
async def generate_image(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    req: ImageGenerateRequest,
):
    """生成图片"""
    try:
        service = ImageGenerationServiceFactory.get_service()
        results = await service.generate(
            prompt=req.prompt,
            width=req.width,
            height=req.height,
            model=req.model,
            n=req.n,
        )
        return ImageGenerateResponse(
            images=[
                {
                    "url": r.url,
                    "b64_json": r.b64_json,
                    "revised_prompt": r.revised_prompt,
                }
                for r in results
            ]
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Image generation failed for user {current_user.id}: {e}")
        raise HTTPException(
            status_code=503,
            detail="Image generation service unavailable",
        ) from e
