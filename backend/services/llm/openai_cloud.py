"""
OpenAI 官方 LLM 服务实现
对接 OpenAI API (https://api.openai.com/v1/chat/completions)
"""

from .openai_compatible import OpenAICompatibleService


class OpenAIService(OpenAICompatibleService):
    """OpenAI 官方 LLM 服务

    复用 OpenAICompatibleService 的完整逻辑，
    默认 base_url 在配置中已设为 https://api.openai.com
    """
    pass
