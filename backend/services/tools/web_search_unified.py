"""Shared web search: Tavily (fast, keyed) + free fallbacks."""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def resolve_tavily_api_key() -> str:
    """Env first, then optional settings DB fields."""
    for k in ("TAVILY_API_KEY", "SEARCH_API_KEY", "tavily_api_key"):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    try:
        from backend.core.config import settings

        for attr in ("tavily_api_key", "search_api_key"):
            v = (getattr(settings, attr, None) or "").strip()
            if v:
                return v
    except Exception:
        pass
    return ""


async def tavily_search(query: str, max_results: int = 5, *, timeout: float = 8.0) -> str:
    """Tavily API. Empty string if no key / fail (caller falls back)."""
    key = resolve_tavily_api_key()
    if not key:
        return ""
    q = (query or "").strip()
    if not q:
        return ""
    n = max(1, min(int(max_results or 5), 15))
    try:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": key,
                    "query": q,
                    "max_results": n,
                    "include_answer": True,
                    "search_depth": "basic",
                },
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status >= 400:
                    logger.info("tavily HTTP %s", resp.status)
                    return ""
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
    except Exception as e:
        logger.info("tavily search failed: %s", e)
    return ""


async def web_search_unified(query: str, max_results: int = 5) -> str:
    """Tavily first (if key), else free waterfall."""
    q = (query or "").strip()
    if not q:
        return "[Error] query is required"
    n = max(1, min(int(max_results or 5), 15))

    tv = await tavily_search(q, n, timeout=8.0)
    if tv:
        return tv

    from backend.services.tools.free_search import free_web_search

    return await free_web_search(q, n)


__all__ = ["resolve_tavily_api_key", "tavily_search", "web_search_unified"]
