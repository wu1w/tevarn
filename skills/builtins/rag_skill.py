"""
RAG Skill - 知识库检索
封装 search_knowledge_base 为 Agent 可调用的 Skill
支持混合检索、多 Collection、查询变换
"""

from backend.services.rag import RAGServiceFactory

from ..base import BaseSkill


class SearchKnowledgeBaseSkill(BaseSkill):
    """知识库检索 Skill"""

    name = "search_knowledge_base"
    description = (
        "当用户询问需要专业知识、文档内容或历史资料时，"
        "调用此工具检索本地知识库，获取相关上下文。"
        "支持混合检索（BM25+向量）、多知识库并行检索。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "检索查询语句，应从用户问题中提取核心关键词",
            },
            "top_k": {
                "type": "integer",
                "description": "返回的文档数量（默认 5）",
                "default": 5,
            },
            "collection": {
                "type": "string",
                "description": "指定单个 collection 名（可选）",
            },
            "collections": {
                "type": "array",
                "items": {"type": "string"},
                "description": "指定多个 collection 名（可选，如 ['knowledge', 'wiki']）",
            },
            "search_mode": {
                "type": "string",
                "enum": ["hybrid", "vector", "keyword"],
                "description": "检索模式：hybrid（BM25+向量+RRF，推荐）、vector（纯向量）、keyword（纯关键词）",
                "default": "hybrid",
            },
        },
        "required": ["query"],
    }

    async def execute(
        self,
        query: str,
        top_k: int = 5,
        collection: str | None = None,
        collections: list[str] | None = None,
        search_mode: str | None = None,
        user_id: str | None = None,
    ) -> str:
        """
        执行知识库检索

        Args:
            query: 检索查询
            top_k: 返回文档数
            collection: 单个 collection 名
            collections: 多个 collection 名
            search_mode: hybrid | vector | keyword
            user_id: 当前用户 ID（用于隔离不同用户的知识库）

        Returns:
            格式化后的检索结果上下文
        """
        rag_service = RAGServiceFactory.get_service()
        result = await rag_service.search_knowledge_base(
            query,
            top_k=top_k,
            collection=collection,
            collections=collections,
            search_mode=search_mode,
            user_id=user_id,
        )
        return result
