"""
统一 Skill 元数据模型

跨生态（takton / clawhub / awesome-claude / awesome-hermes）的 skill 统一表示。
所有 fetcher 都将各源原始数据转换为 UnifiedSkill 输出给前端。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


SkillSource = Literal["takton", "clawhub", "awesome-claude", "awesome-hermes", "custom"]


class SkillStats(BaseModel):
    """Skill 统计信息（各源可能不同）"""
    stars: int = 0
    downloads: int = 0
    installs: int = 0
    forks: int = 0
    versions: int = 0


class UnifiedSkill(BaseModel):
    """统一 Skill 元数据"""
    # 核心标识
    id: str                          # 源内唯一 slug（如 "mcp-builder"）
    name: str                        # 机器名（用作安装目录）
    display_name: str                # 展示名

    # 描述
    summary: str = ""                # 一句话简介
    description: str = ""            # 详细描述

    # 源信息
    source: SkillSource = "custom"
    source_url: str = ""             # 该 skill 在源站的详情页
    source_repo: str = ""            # 所在 GitHub 仓库（如 "ComposioHQ/awesome-claude-skills"）

    # 可下载的 SKILL.md 文件
    skill_md_url: str | None = None  # raw 下载链接（None 表示不支持程序化下载）
    skill_md_content: str = ""       # 已下载的内容（可选，预览用）

    # 元数据
    topics: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    license: str | None = None
    author: str = ""
    version: str = ""

    # 统计
    stats: SkillStats = Field(default_factory=SkillStats)

    # 安装相关
    install_command: str = ""        # 各源推荐安装命令（展示用）
    compatibility: list[str] = Field(default_factory=list)  # ["takton","hermes","claude-code","openclaw"]

    # 时间
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # 源原生数据（debug 用）
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)


class SkillStoreQuery(BaseModel):
    """商店查询参数"""
    source: SkillSource | None = None     # None = 所有源
    search: str = ""                       # 搜索关键词（匹配 name/summary/topics）
    topic: str = ""                        # 按 topic 过滤
    limit: int = 50
    offset: int = 0


class SkillStoreResponse(BaseModel):
    """商店列表响应"""
    items: list[UnifiedSkill]
    total: int
    sources: list[SkillSource]             # 本次响应包含的源
    errors: dict[str, str] = Field(default_factory=dict)  # 源 -> 错误信息（部分源失败时）
