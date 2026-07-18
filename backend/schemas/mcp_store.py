"""
统一 MCP 商店元数据（跨生态）。

MCP 与 Skills 不同：Claude Code / Hermes / OpenClaw / Codex 共享同一 MCP 协议，
真正的公共池是 Official MCP Registry（registry.modelcontextprotocol.io）。
Takton 精选目录 + 官方 Registry 一起聚合，安装时映射为本机 stdio/sse 配置。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MCPStoreSource = Literal["curated", "official", "custom"]


class UnifiedMCP(BaseModel):
    """统一 MCP Server 目录项。"""

    id: str
    name: str
    display_name: str
    summary: str = ""
    description: str = ""
    source: MCPStoreSource = "curated"
    source_url: str = ""
    icon: str = "🔌"
    category: str = "其他"
    tags: list[str] = Field(default_factory=list)

    # 安装映射（写入 Takton MCP 配置）
    transport: Literal["stdio", "sse"] = "stdio"
    command: str = ""
    args: list[str] = Field(default_factory=list)
    url: str = ""
    env_hint: str = ""  # 展示/预填 KEY= 提示，不含密钥
    risk_level: str = "medium"
    version: str = ""
    registry_type: str = ""  # npm | pypi | remote | ...
    package_id: str = ""
    popularity: int = 0
    compatibility: list[str] = Field(
        default_factory=lambda: ["takton", "claude-code", "hermes", "openclaw", "codex"]
    )
    installable: bool = True
    note: str = ""

    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)


class MCPStoreSourceInfo(BaseModel):
    id: str
    name: str
    description: str = ""
    enabled: bool = True
    error: str | None = None
    count: int = 0


class MCPStoreListResponse(BaseModel):
    items: list[UnifiedMCP]
    total: int
    sources: list[MCPStoreSourceInfo] = Field(default_factory=list)
    query: str = ""
