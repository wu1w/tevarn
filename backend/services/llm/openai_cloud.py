"""
OpenAI 官方 LLM 服务实现
对接 OpenAI API (https://api.openai.com/v1/chat/completions)
"""

from .openai_compatible import OpenAICompatibleService
from .provider_profiles import resolve_profile


class OpenAIService(OpenAICompatibleService):
    """OpenAI 官方 LLM 服务

    复用 OpenAICompatibleService 的完整逻辑，
    默认 base_url 在配置中已设为 https://api.openai.com
    """

    def __init__(self, config=None, *, profile=None, provider_id: str | None = None,
                 prompt_cache_key: str | None = None):
        prof = profile or resolve_profile(
            provider_id=provider_id or "openai",
            base_url=getattr(config, "base_url", None) if config else None,
            model=getattr(config, "model", None) if config else None,
            llm_provider="openai",
        )
        super().__init__(
            config,
            profile=prof,
            provider_id=provider_id or "openai",
            prompt_cache_key=prompt_cache_key,
        )
