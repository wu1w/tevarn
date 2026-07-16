"""
Reranker 服务抽象接口
统一封装重排序服务
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class RerankedResult:
    """精排后的结果"""

    text: str
    score: float
    original_index: int


class RerankerService(ABC):
    """
    Reranker 服务抽象基类

    所有 Reranker 后端（本地 / Cohere）需实现此接口
    """

    @abstractmethod
    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_n: int = 5,
    ) -> list[RerankedResult]:
        """
        对文档列表进行重排序

        Args:
            query: 查询文本
            documents: 待精排的文档文本列表
            top_n: 最终返回数量

        Returns:
            精排后的结果列表
        """
        raise NotImplementedError
