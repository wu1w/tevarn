"""
图片生成服务抽象接口
统一封装图片生成服务
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ImageResult:
    """图片生成结果"""

    url: str | None = None
    b64_json: str | None = None
    revised_prompt: str | None = None


class ImageGenerationService(ABC):
    """
    图片生成服务抽象基类

    所有图片生成后端（OpenAI DALL-E / 本地 Stable Diffusion 等）需实现此接口
    """

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        model: str | None = None,
        n: int = 1,
    ) -> list[ImageResult]:
        """
        生成图片

        Args:
            prompt: 图片生成提示词
            width: 图片宽度
            height: 图片高度
            model: 指定模型（可选）
            n: 生成数量

        Returns:
            图片结果列表
        """
        raise NotImplementedError
