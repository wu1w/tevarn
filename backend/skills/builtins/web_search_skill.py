"""
Web Search Skill — Tavily(可选 Key) + 免配置开源 fallback 瀑布
"""

from __future__ import annotations

import os
from typing import Any

from ..base import BaseSkill


class WebSearchSkill(BaseSkill):
    """网页搜索 Skill"""

    name = "web_search"
    description = (
        "获取最新信息、新闻或网络资源时必须调用。"
        "无需 API Key 也可搜索（DuckDuckGo/Bing/Wikipedia 等开源 fallback）。"
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
        errors: list[str] = []

        # 1) Tavily（有 Key 时优先）
        tavily_key = (
            os.environ.get("TAVILY_API_KEY")
            or os.environ.get("SEARCH_API_KEY")
            or ""
        ).strip()
        if tavily_key:
            try:
                import aiohttp

                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        "https://api.tavily.com/search",
                        json={
                            "api_key": tavily_key,
                            "query": q,
                            "max_results": n,
                            "include_answer": True,
                        },
                        timeout=aiohttp.ClientTimeout(total=25),
                    ) as resp:
                        data = await resp.json(content_type=None)
                        if resp.status < 400:
                            lines = [f"# Search: {q} (tavily)"]
                            if data.get("answer"):
                                lines.append(f"Answer: {data['answer']}")
                            for i, r in enumerate(data.get("results") or [], 1):
                                lines.append(
                                    f"{i}. {r.get('title') or ''}\n"
                                    f"   {r.get('url') or ''}\n"
                                    f"   {(r.get('content') or '')[:240]}"
                                )
                            if len(lines) > 1:
                                return "\n".join(lines)
                        else:
                            errors.append(f"tavily HTTP {resp.status}")
            except Exception as e:
                errors.append(f"tavily: {e}")

        # 2) 免 Key 瀑布（ddgs / DDG lite / Bing HTML / Wiki …）
        try:
            from backend.services.tools.free_search import free_web_search

            text = await free_web_search(q, n)
            if text and not text.startswith("[Error]"):
                return text
            if text:
                errors.append(text.replace("\n", " ")[:200])
        except Exception as e:
            errors.append(f"free_search: {e}")

        # 3) 旧 execute_search 兜底
        try:
            from backend.services.tools.executors import execute_search

            result = await execute_search({}, {"query": q, "max_results": n})
            if result and not str(result).startswith("[Error]") and "No results" not in result:
                return result
            errors.append(str(result)[:160])
        except Exception as e:
            errors.append(f"execute_search: {e}")

        return (
            f"[Error] web_search failed for «{q}».\n"
            f"details: {' | '.join(errors)}\n"
            "Engines tried: tavily(optional), ddgs, ddg-lite, bing-html, wikipedia.\n"
            "No Brave/Tavily key required for fallback."
        )
