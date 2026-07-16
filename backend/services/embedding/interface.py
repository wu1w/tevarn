"""
Embedding 服务抽象接口
统一封装文本向量化服务
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class EmbeddingResult:
    """向量化结果"""

    text: str
    vector: list[float]


class EmbeddingService(ABC):
    """
    Embedding 服务抽象基类

    所有 Embedding 后端（Ollama / OpenAI / 本地兼容）需实现此接口
    """

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """
        将文本列表编码为向量列表

        Args:
            texts: 文本列表

        Returns:
            向量列表，每个向量对应一个输入文本
        """
        raise NotImplementedError

    @abstractmethod
    async def embed_query(self, query: str) -> list[float]:
        """
        将单个查询文本编码为向量

        Args:
            query: 查询文本

        Returns:
            向量
        """
        raise NotImplementedError
