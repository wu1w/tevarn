"""
RAG Skill - 知识库检索
向量栈未就绪时返回结构化降级说明（不假装检索成功）。
"""

from backend.services.rag import RAGServiceFactory
from backend.services.rag.capability import get_rag_status

from ..base import BaseSkill


class SearchKnowledgeBaseSkill(BaseSkill):
    """知识库检索 Skill"""

    name = "search_knowledge_base"
    description = (
        "当用户询问需要专业知识、文档内容或历史资料时，"
        "调用此工具检索本地知识库，获取相关上下文。"
        "支持混合检索（BM25+向量）、多知识库并行检索。"
        "若返回「本地模式/不可用」，请改用 Wiki、memory 文件或工作区文件工具。"
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
        **kwargs,
    ) -> str:
        """搜索知识库"""
        # 兼容 Agent Loop 注入的 _session_id 等元数据，忽略即可
        st = get_rag_status()
        if not st.tool_search:
            import json

            return json.dumps(
                {
                    "ok": False,
                    "code": "RAG_LOCAL_MODE",
                    "mode": st.mode,
                    "message": st.reason,
                    "hints": st.hints,
                    "fallback": [
                        "使用工作区 memory.md / memory_temp.md / memory/日期.md",
                        "使用 Wiki 图谱相关实体",
                        "使用文件工具阅读项目文档",
                    ],
                },
                ensure_ascii=False,
            )

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
