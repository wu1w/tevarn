"""
HTTP GET Skill - 发送 HTTP GET 请求
"""

from backend.core.net_safety import UnsafeURLError, validate_public_url

from ..base import BaseSkill


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
        """发送 HTTP GET 请求"""
        # 兼容 Agent Loop 注入的 user_id / _session_id 等元数据，忽略即可
        try:
            validate_public_url(url)
        except UnsafeURLError as e:
            return f"[Security Blocked] {e}"

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
