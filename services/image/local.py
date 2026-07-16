"""
本地图片生成服务实现
支持任何遵循 OpenAI /v1/images/generations 格式的本地服务
如 Stable Diffusion WebUI (with --api) / ComfyUI / Fooocus 等
"""

import logging
from typing import Any

import aiohttp

from backend.core.config import settings

from .interface import ImageGenerationService, ImageResult

logger = logging.getLogger(__name__)


class LocalImageService(ImageGenerationService):
    """本地 OpenAI 兼容图片生成服务"""

    def __init__(self, config=None):
        if config is None:
            config = settings
        self.base_url = getattr(config, "image_base_url", "http://localhost:7860").rstrip("/")
        self.model = getattr(config, "image_model", "sd-xl")
        self.api_key = getattr(config, "image_api_key", None)

    def _get_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def generate(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        model: str | None = None,
        n: int = 1,
    ) -> list[ImageResult]:
        """调用本地 OpenAI 兼容 /v1/images/generations"""
        url = f"{self.base_url}/v1/images/generations"
        payload: dict[str, Any] = {
            "model": model or self.model,
            "prompt": prompt,
            "n": min(n, 4),
            "size": f"{width}x{height}",
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
            logger.error(f"Local image generation error: {e}")
            raise
