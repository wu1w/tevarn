"""
OpenAI 图片生成服务实现
对接 OpenAI DALL-E 3 API (/v1/images/generations)
"""

import logging
from typing import Any

import aiohttp

from backend.core.config import settings

from .interface import ImageGenerationService, ImageResult

logger = logging.getLogger(__name__)


class OpenAIImageService(ImageGenerationService):
    """OpenAI DALL-E 图片生成服务"""

    def __init__(self, config=None):
        if config is None:
            config = settings
        self.base_url = getattr(config, "image_base_url", "https://api.openai.com").rstrip("/")
        self.model = getattr(config, "image_model", "dall-e-3")
        self.api_key = getattr(config, "image_api_key", None)

    def _get_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _get_size(self, width: int, height: int) -> str:
        """将宽高映射为 OpenAI 支持的 size 格式"""
        # OpenAI 只支持特定尺寸
        size_map = {
            (1024, 1024): "1024x1024",
            (1792, 1024): "1792x1024",
            (1024, 1792): "1024x1792",
        }
        return size_map.get((width, height), "1024x1024")

    async def generate(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        model: str | None = None,
        n: int = 1,
    ) -> list[ImageResult]:
        """调用 OpenAI /v1/images/generations"""
        url = f"{self.base_url}/v1/images/generations"
        effective_model = model or self.model
        # DALL-E 3 每次请求只支持 n=1，DALL-E 2 最多支持 n=10
        max_n = 1 if effective_model == "dall-e-3" else 10
        payload: dict[str, Any] = {
            "model": effective_model,
            "prompt": prompt,
            "n": min(n, max_n),
            "size": self._get_size(width, height),
            "response_format": "url",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=self._get_headers()) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    results = []
                    for item in data.get("data", []):
                        results.append(
                            ImageResult(
                                url=item.get("url"),
                                b64_json=item.get("b64_json"),
                                revised_prompt=item.get("revised_prompt"),
                            )
                        )
                    return results
        except Exception as e:
            logger.error(f"OpenAI image generation error: {e}")
            raise
