"""
MCP Store：精选目录 + 官方 Registry 聚合（对标 skill_store 思路）。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

import httpx

from backend.schemas.mcp_store import MCPStoreListResponse, MCPStoreSourceInfo, UnifiedMCP

logger = logging.getLogger(__name__)

OFFICIAL_REGISTRY = "https://registry.modelcontextprotocol.io"
CACHE_TTL = 300.0

# ─── Takton 精选（含 Tavily；跨 Claude/Hermes/OpenClaw/Codex 通用） ───

CURATED: list[UnifiedMCP] = [
    UnifiedMCP(
        id="tavily",
        name="tavily",
        display_name="Tavily Search",
        summary="AI 优化的实时网页搜索与内容提取",
        description=(
            "Tavily MCP：web search / extract / news。适合 Agent 调研与联网问答。"
            "需配置 TAVILY_API_KEY（https://tavily.com）。"
            "兼容 Claude Code / Hermes / OpenClaw / Codex / Takton。"
        ),
        source="curated",
        source_url="https://github.com/tavily-ai/tavily-mcp",
        icon="🔎",
        category="搜索",
        tags=["search", "web", "tavily", "research"],
        transport="stdio",
        command="npx",
        args=["-y", "tavily-mcp@latest"],
        env_hint="TAVILY_API_KEY=",
        risk_level="low",
        version="latest",
        registry_type="npm",
        package_id="tavily-mcp",
        popularity=20000,
    ),
    UnifiedMCP(
        id="github",
        name="github",
        display_name="GitHub",
        summary="仓库、PR、Issues、Actions",
        description="官方 GitHub MCP。需 GITHUB_PERSONAL_ACCESS_TOKEN。",
        source="curated",
        source_url="https://github.com/modelcontextprotocol/servers",
        icon="🐙",
        category="开发工具",
        tags=["git", "devops"],
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        env_hint="GITHUB_PERSONAL_ACCESS_TOKEN=",
        risk_level="medium",
        package_id="@modelcontextprotocol/server-github",
        registry_type="npm",
        popularity=12000,
    ),
    UnifiedMCP(
        id="filesystem",
        name="filesystem",
        display_name="Filesystem",
        summary="本地文件读写与搜索",
        description="本地文件系统 MCP。建议配置 allowed_paths。",
        source="curated",
        icon="📁",
        category="文件管理",
        tags=["files", "local"],
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem"],
        risk_level="high",
        package_id="@modelcontextprotocol/server-filesystem",
        registry_type="npm",
        popularity=15000,
    ),
    UnifiedMCP(
        id="brave-search",
        name="brave-search",
        display_name="Brave Search",
        summary="网页 / 新闻搜索",
        description="Brave Search API。需 BRAVE_API_KEY。",
        source="curated",
        icon="🔍",
        category="搜索",
        tags=["search", "web"],
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-brave-search"],
        env_hint="BRAVE_API_KEY=",
        risk_level="low",
        package_id="@modelcontextprotocol/server-brave-search",
        registry_type="npm",
        popularity=9000,
    ),
    UnifiedMCP(
        id="memory",
        name="memory",
        display_name="Memory",
        summary="知识图谱式持久记忆",
        description="实体关系记忆 MCP。",
        source="curated",
        icon="🧠",
        category="记忆",
        tags=["memory"],
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-memory"],
        risk_level="low",
        package_id="@modelcontextprotocol/server-memory",
        registry_type="npm",
        popularity=5000,
    ),
    UnifiedMCP(
        id="fetch",
        name="fetch",
        display_name="Fetch",
        summary="抓取网页为可读文本",
        description="官方 HTTP fetch MCP（PyPI: mcp-server-fetch）。用 uvx 启动。",
        source="curated",
        icon="🌐",
        category="搜索",
        tags=["http", "web"],
        transport="stdio",
        command="uvx",
        args=["mcp-server-fetch"],
        risk_level="low",
        package_id="mcp-server-fetch",
        registry_type="pypi",
        popularity=7800,
    ),
    UnifiedMCP(
        id="postgres",
        name="postgres",
        display_name="PostgreSQL",
        summary="SQL 查询",
        description="Postgres MCP。请用只读账号。",
        source="curated",
        icon="🐘",
        category="数据库",
        tags=["sql", "db"],
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-postgres"],
        env_hint="POSTGRES_CONNECTION_STRING=",
        risk_level="high",
        package_id="@modelcontextprotocol/server-postgres",
        registry_type="npm",
        popularity=6000,
    ),
    UnifiedMCP(
        id="puppeteer",
        name="puppeteer",
        display_name="Puppeteer",
        summary="无头浏览器自动化",
        description="页面打开、截图、点击填表。",
        source="curated",
        icon="🎭",
        category="浏览器",
        tags=["browser"],
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-puppeteer"],
        risk_level="high",
        package_id="@modelcontextprotocol/server-puppeteer",
        registry_type="npm",
        popularity=5500,
    ),
    UnifiedMCP(
        id="slack",
        name="slack",
        display_name="Slack",
        summary="消息与频道",
        description="需 SLACK_BOT_TOKEN。",
        source="curated",
        icon="💬",
        category="通讯协作",
        tags=["chat"],
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-slack"],
        env_hint="SLACK_BOT_TOKEN=",
        risk_level="medium",
        package_id="@modelcontextprotocol/server-slack",
        registry_type="npm",
        popularity=8000,
    ),
    UnifiedMCP(
        id="sequential-thinking",
        name="sequential-thinking",
        display_name="Sequential Thinking",
        summary="分步推理工具",
        description="结构化逐步思考。",
        source="curated",
        icon="🧩",
        category="其他",
        tags=["reasoning"],
        transport="stdio",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-sequential-thinking"],
        risk_level="safe",
        package_id="@modelcontextprotocol/server-sequential-thinking",
        registry_type="npm",
        popularity=3900,
    ),
]


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", (name or "").strip()).strip("-").lower()
    return s[:80] or "mcp"


def _risk_from_env(env_vars: list[dict] | None) -> str:
    if not env_vars:
        return "low"
    secrets = [e for e in env_vars if e.get("isSecret") or e.get("isRequired")]
    if len(secrets) >= 2:
        return "medium"
    return "low" if secrets else "safe"


def _map_official_item(entry: dict[str, Any]) -> UnifiedMCP | None:
    server = entry.get("server") or entry
    if not isinstance(server, dict):
        return None
    full_name = server.get("name") or ""
    title = server.get("title") or full_name.split("/")[-1] or full_name
    desc = server.get("description") or ""
    version = server.get("version") or ""
    packages = server.get("packages") or []
    remotes = server.get("remotes") or []
    repo = (server.get("repository") or {}) if isinstance(server.get("repository"), dict) else {}
    source_url = repo.get("url") or server.get("websiteUrl") or ""

    transport: str = "stdio"
    command = ""
    args: list[str] = []
    url = ""
    env_hint = ""
    registry_type = ""
    package_id = ""
    installable = False
    note = ""

    if packages:
        pkg = packages[0]
        registry_type = (pkg.get("registryType") or "").lower()
        package_id = pkg.get("identifier") or ""
        ver = pkg.get("version") or "latest"
        pkg_transport = ((pkg.get("transport") or {}).get("type") or "stdio").lower()
        env_vars = pkg.get("environmentVariables") or []
        for ev in env_vars:
            n = ev.get("name")
            if n:
                env_hint += f"{n}=\n"
        env_hint = env_hint.strip()
        if pkg_transport in ("stdio",):
            transport = "stdio"
            if registry_type == "npm" and package_id:
                command = "npx"
                args = ["-y", f"{package_id}@{ver}" if ver and ver != "latest" else f"{package_id}@latest"]
                installable = True
            elif registry_type in ("pypi", "python") and package_id:
                command = "uvx"
                args = [package_id] if not ver or ver == "latest" else [f"{package_id}=={ver}"]
                installable = True
            else:
                note = f"暂不支持 registryType={registry_type} 的一键安装，请自定义配置"
        else:
            note = f"包 transport={pkg_transport}，Takton 当前主支持 stdio/sse"
    elif remotes:
        remote = remotes[0]
        rtype = (remote.get("type") or "").lower()
        url = remote.get("url") or ""
        # streamable-http / sse 均尝试以 sse URL 接入
        if url and rtype in ("sse", "streamable-http", "http"):
            transport = "sse"
            installable = True
            registry_type = "remote"
            if rtype == "streamable-http":
                note = "官方为 streamable-http，已映射为 SSE URL（若连接失败请用自定义）"
        else:
            note = f"远程类型 {rtype} 暂不支持一键安装"

    if not installable and not note:
        note = "缺少 packages/remotes 安装信息"

    sid = _slug(full_name.replace("/", "__"))
    return UnifiedMCP(
        id=sid,
        name=sid,
        display_name=title,
        summary=desc[:160],
        description=desc,
        source="official",
        source_url=source_url,
        icon="📦",
        category="官方 Registry",
        tags=["official-registry", full_name.split("/")[0] if "/" in full_name else "mcp"],
        transport=transport if transport in ("stdio", "sse") else "stdio",  # type: ignore
        command=command,
        args=args,
        url=url,
        env_hint=env_hint,
        risk_level=_risk_from_env(packages[0].get("environmentVariables") if packages else None),
        version=version,
        registry_type=registry_type,
        package_id=package_id,
        popularity=100,
        installable=installable,
        note=note,
    )


class MCPStoreService:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, list[UnifiedMCP]]] = {}
        self._errors: dict[str, str | None] = {}

    async def _fetch_official(self, limit: int = 80, search: str = "") -> list[UnifiedMCP]:
        key = f"official:{search}:{limit}"
        now = time.time()
        hit = self._cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]

        params: dict[str, Any] = {"limit": min(limit, 100)}
        if search:
            params["search"] = search
        items: list[UnifiedMCP] = []
        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                r = await client.get(f"{OFFICIAL_REGISTRY}/v0/servers", params=params)
                r.raise_for_status()
                data = r.json()
            for entry in data.get("servers") or []:
                m = _map_official_item(entry)
                if m:
                    items.append(m)
            self._errors["official"] = None
        except Exception as e:
            logger.warning("official MCP registry fetch failed: %s", e)
            self._errors["official"] = f"{type(e).__name__}: {e}"
            items = []

        self._cache[key] = (now, items)
        return items

    def _curated(self, search: str = "") -> list[UnifiedMCP]:
        items = list(CURATED)
        self._errors["curated"] = None
        if not search:
            return items
        kw = search.lower()
        return [
            m
            for m in items
            if kw in m.name.lower()
            or kw in m.display_name.lower()
            or kw in m.summary.lower()
            or kw in m.description.lower()
            or any(kw in t.lower() for t in m.tags)
        ]

    async def list_sources(self) -> list[MCPStoreSourceInfo]:
        # 轻量探测官方
        official = await self._fetch_official(limit=5)
        return [
            MCPStoreSourceInfo(
                id="curated",
                name="Takton 精选",
                description="含 Tavily 等常用 MCP；Claude/Hermes/OpenClaw/Codex 通用",
                enabled=True,
                error=self._errors.get("curated"),
                count=len(CURATED),
            ),
            MCPStoreSourceInfo(
                id="official",
                name="Official MCP Registry",
                description="registry.modelcontextprotocol.io — 跨生态公共目录",
                enabled=True,
                error=self._errors.get("official"),
                count=len(official) if official else 0,
            ),
        ]

    async def list_items(
        self,
        source: str | None = None,
        search: str = "",
        limit: int = 48,
        offset: int = 0,
    ) -> MCPStoreListResponse:
        search = (search or "").strip()
        tasks = []
        want_curated = source in (None, "", "all", "curated")
        want_official = source in (None, "", "all", "official")

        curated: list[UnifiedMCP] = []
        official: list[UnifiedMCP] = []

        if want_curated:
            curated = self._curated(search)
        if want_official:
            official = await self._fetch_official(limit=100, search=search)

        # 合并：精选优先，官方去重（按 source+id 唯一，避免 React key 冲突）
        seen: set[str] = set()
        merged: list[UnifiedMCP] = []
        for m in curated + official:
            key = f"{m.source}/{m.id}".lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(m)

        total = len(merged)
        page = merged[offset : offset + limit]
        sources = await self.list_sources()
        return MCPStoreListResponse(
            items=page,
            total=total,
            sources=sources,
            query=search,
        )

    def get_item(self, source: str, item_id: str) -> UnifiedMCP | None:
        if source == "curated":
            for m in CURATED:
                if m.id == item_id or m.name == item_id:
                    return m
        # official may be cached under various keys — scan all caches
        for (_k, (_t, items)) in self._cache.items():
            for m in items:
                if m.id == item_id or m.name == item_id:
                    return m
        # fallback search curated again + try fetch
        return None

    async def resolve_item(self, source: str, item_id: str) -> UnifiedMCP | None:
        hit = self.get_item(source, item_id)
        if hit:
            return hit
        if source in ("official", "all", ""):
            # 用 id 或 search 再拉
            items = await self._fetch_official(limit=50, search=item_id.replace("__", " "))
            for m in items:
                if m.id == item_id or m.name == item_id:
                    return m
            for m in items:
                if item_id in m.id or item_id in m.name:
                    return m
        for m in CURATED:
            if m.id == item_id or m.name == item_id:
                return m
        return None


_service: MCPStoreService | None = None


def get_mcp_store_service() -> MCPStoreService:
    global _service
    if _service is None:
        _service = MCPStoreService()
    return _service
