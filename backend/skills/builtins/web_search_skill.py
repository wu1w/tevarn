"""
Web Search Skill — Tavily(优先) + 免配置开源 fallback
"""
from __future__ import annotations

from typing import Any

from ..base import BaseSkill


class WebSearchSkill(BaseSkill):
    """网页搜索 Skill"""

    name = "web_search"
    description = (
        "获取最新信息、新闻或网络资源时必须调用。"
        "有 TAVILY_API_KEY 时优先 Tavily（快）；否则 DuckDuckGo/Bing/Wikipedia fallback。"
        "返回标题/链接/摘要。不要声称搜索不可用——直接调用本工具。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词",
            },
            "num_results": {
                "type": "integer",
                "description": "返回结果数量（默认 5）",
                "default": 5,
            },
        },
        "required": ["query"],
    }

    async def execute(self, query: str = "", num_results: int = 5, **kwargs: Any) -> str:
        q = (query or kwargs.get("q") or "").strip()
        if not q:
            return "[Error] query is required"
        n = int(num_results or kwargs.get("max_results") or 5)
        n = max(1, min(n, 15))
        from backend.services.tools.web_search_unified import web_search_unified

        return await web_search_unified(q, n)
