"""
LLM 服务抽象接口
统一封装 Ollama / vLLM 等本地 LLM API
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from .schemas import LLMChunk, LLMResponse


class LLMService(ABC):
    """
    LLM 服务抽象基类

    所有 LLM 后端（Ollama / vLLM）需实现此接口，
    提供统一的 chat 方法，支持流式输出和工具调用
    """

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = True,
    ) -> AsyncIterator[LLMChunk]:
        """
        调用 LLM 进行对话

        Args:
            messages: OpenAI 格式的消息列表
                [{"role": "system"/"user"/"assistant"/"tool", "content": "..."}]
            tools: 工具定义列表（JSON Schema 格式）
            stream: 是否启用流式输出

        Yields:
            LLMChunk: 流式输出块
        """
        raise NotImplementedError

    @abstractmethod
    async def chat_complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """
        非流式对话，返回完整响应

        Args:
            messages: 消息列表
            tools: 工具定义列表

        Returns:
            LLMResponse: 完整响应
        """
        raise NotImplementedError
