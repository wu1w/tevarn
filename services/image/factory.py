"""
图片生成服务工厂
根据配置自动选择 OpenAI / 本地兼容后端
"""

import logging

from backend.core.config import settings

from .interface import ImageGenerationService
from .local import LocalImageService
from .openai import OpenAIImageService

logger = logging.getLogger(__name__)


class ImageGenerationServiceFactory:
    """图片生成服务工厂类"""

    _instance: ImageGenerationService | None = None

    @classmethod
    def get_service(cls) -> ImageGenerationService:
        """获取图片生成服务单例"""
        if cls._instance is None:
            cls._instance = cls._create_service()
        return cls._instance

    @classmethod
    def _create_service(cls) -> ImageGenerationService:
        """根据 IMAGE_PROVIDER 配置创建对应服务"""
        provider = settings.image_provider

        if provider == "openai":
            logger.info(f"Using OpenAI image generation: {settings.image_base_url}/{settings.image_model}")
            return OpenAIImageService()
        elif provider == "openai-compatible":
            logger.info(f"Using OpenAI-Compatible image generation: {settings.image_base_url}/{settings.image_model}")
            return LocalImageService()
        else:
            logger.warning(f"Unknown image provider '{provider}', falling back to local")
            return LocalImageService()

    @classmethod
    def reset(cls) -> None:
        """重置单例（主要用于测试）"""
        cls._instance = None
