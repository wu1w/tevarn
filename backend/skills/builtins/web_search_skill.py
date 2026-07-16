"""
Web Search Skill - 网页搜索
当前为桩实现，后续可接入 SerpAPI / Bing API / Google CSE
"""

from ..base import BaseSkill


class WebSearchSkill(BaseSkill):
    """网页搜索 Skill"""

    name = "web_search"
    description = (
        "当需要获取最新信息、时事新闻或网络资源时，"
        "调用此工具执行网页搜索。"
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

    async def execute(self, query: str, num_results: int = 5, **kwargs) -> str:
        """执行网页搜索（桩实现）"""
        # 兼容 Agent Loop 注入的 user_id / _session_id 等元数据，忽略即可
        return (
            f"[Web Search Stub] Query: '{query}'\n"
            f"Results: {num_results}\n"
            f"⚠️ 这是桩实现。请在 .env 中配置 SEARCH_API_KEY 并接入真实搜索服务。"
        )
