"""网页摘要 — 抓取公开网页正文片段，供对话总结。"""

from __future__ import annotations

import re

import httpx

from ..base import BaseSkill


class SummarizeWebpageSkill(BaseSkill):
    name = "fetch_webpage"
    description = (
        "抓取公开网页的文字内容（截断后返回），便于向用户解释页面要点。"
        "当用户说「帮我看看这个链接」「这个网页讲什么」时调用。"
        "不要用于需要登录的页面。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "完整 URL，以 http:// 或 https:// 开头"},
            "max_chars": {
                "type": "integer",
                "description": "返回最大字符数，默认 6000",
                "default": 6000,
            },
        },
        "required": ["url"],
    }

    async def execute(self, url: str, max_chars: int = 6000, **kwargs) -> str:
        if not url or not re.match(r"^https?://", url.strip(), re.I):
            return "请提供以 http:// 或 https:// 开头的公开链接。"
        max_chars = max(500, min(int(max_chars or 6000), 20000))
        try:
            async with httpx.AsyncClient(
                timeout=25.0,
                follow_redirects=True,
                headers={"User-Agent": "TaktonAgent/0.1 (+local)"},
            ) as client:
                r = await client.get(url.strip())
                r.raise_for_status()
                text = r.text
        except Exception as e:
            return f"抓取失败: {e}"

        # strip scripts/styles tags roughly
        text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", text)
        text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
        text = re.sub(r"(?is)<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n…(已截断)"
        return f"URL: {url}\n长度: {len(text)} 字符\n---\n{text}"
