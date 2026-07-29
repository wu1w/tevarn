"""
HTTP GET Skill - 发送 HTTP GET 请求
"""

import logging

from backend.core.net_safety import check_agent_url

from ..base import BaseSkill

logger = logging.getLogger(__name__)


class HttpGetSkill(BaseSkill):
    """HTTP GET 请求 Skill"""

    name = "http_get"
    description = (
        "当需要获取网页内容、API 数据或外部资源时，"
        "调用此工具发送 HTTP GET 请求。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "请求地址",
            },
            "headers": {
                "type": "object",
                "description": "自定义请求头",
                "default": {},
            },
        },
        "required": ["url"],
    }

    async def execute(self, url: str, headers: dict | None = None, **kwargs) -> str:
        """发送 HTTP GET 请求。

        与 http/browser 工具同口径：分层网络策略（硬拦云元数据，
        私网/回环默认放行并记审计）。此前用 validate_public_url 会
        把 localhost/LAN 全拦死，和本地优先产品定位冲突。
        """
        # 兼容 Agent Loop 注入的 user_id / _session_id 等元数据，忽略即可
        allowed, note = check_agent_url(url)
        if not allowed:
            return f"[Security Blocked] {note}"
        if note:
            logger.info("http_get audit: %s url=%s", note, url[:200])

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=headers or {},
                    timeout=aiohttp.ClientTimeout(total=30),
                    allow_redirects=False,
                    max_field_size=8190,
                ) as resp:
                    if resp.status in (301, 302, 303, 307, 308):
                        return f"[Blocked] Redirects are not followed for security reasons (status {resp.status}, location={resp.headers.get('Location', '')})"
                    content = await resp.content.read(8000)
                    text = content.decode("utf-8", errors="replace")
                    return f"Status: {resp.status}\n\n{text}"
        except ImportError:
            return "[Error] aiohttp is not installed. Run: pip install aiohttp"
        except Exception as e:
            return f"[Error] {e}"
