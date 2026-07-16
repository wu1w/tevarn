"""
Wiki 标准实体类型与关系类型定义（LLM-Wiki 底层 Schema）
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class WikiSchema:
    """Wiki 图谱标准 Schema

    实体类型覆盖技术知识表示的常用维度。
    关系类型覆盖：依赖、组成、使用、解决、相关、替代、属于、参与、来源、发布。
    """

    ENTITY_TYPES: tuple[str, ...] = (
        "person",          # 人物
        "organization",    # 组织 / 公司 / 团队
        "project",         # 项目 / 产品 / 仓库
        "tech",            # 技术 / 工具 / 框架 / 语言
        "concept",         # 概念 / 理论 / 方法论
        "docs",            # 文档 / 文章 / 论文 / 书籍
        "event",           # 事件 / 会议 / 版本发布
        "location",        # 地点 / 区域 / 环境
        "problem",         # 问题 / Bug / 挑战 / 痛点
        "solution",        # 方案 / 策略 / 最佳实践
    )

    RELATION_TYPES: tuple[str, ...] = (
        "depends_on",      # A 依赖 B
        "part_of",         # A 是 B 的组成部分
        "uses",            # A 使用 B
        "solves",          # A 解决 B
        "related_to",      # A 与 B 相关
        "alternative_to",  # A 是 B 的替代方案
        "belongs_to",      # A 属于 B
        "participates_in", # A 参与 B
        "authored_by",     # A 由 B 创作 / 发布
        "presents",        # A 介绍 / 展示 B
    )

    ENTITY_TYPE_LABELS: dict[str, str] = {
        "person": "人物",
        "organization": "组织",
        "project": "项目",
        "tech": "技术",
        "concept": "概念",
        "docs": "文档",
        "event": "事件",
        "location": "地点",
        "problem": "问题",
        "solution": "方案",
    }

    RELATION_TYPE_LABELS: dict[str, str] = {
        "depends_on": "依赖",
        "part_of": "属于",
        "uses": "使用",
        "solves": "解决",
        "related_to": "相关",
        "alternative_to": "替代",
        "belongs_to": "归属",
        "participates_in": "参与",
        "authored_by": "作者",
        "presents": "介绍",
    }


class WikiExtractedEntity(BaseModel):
    """LLM 抽取的实体"""

    name: str = Field(..., min_length=1, max_length=128)
    entity_type: str = Field(..., description="标准实体类型之一")
    description: str = ""
    aliases: list[str] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        self.name = self.name.strip()
        self.entity_type = self.entity_type.strip().lower().replace(" ", "_").replace("-", "_")
        if self.entity_type not in WikiSchema.ENTITY_TYPES:
            self.entity_type = "concept"
        self.aliases = [a.strip() for a in self.aliases if a and a.strip()]


class WikiExtractedRelation(BaseModel):
    """LLM 抽取的关系"""

    source_name: str = Field(..., min_length=1)
    target_name: str = Field(..., min_length=1)
    relation_type: str = Field(..., description="标准关系类型之一")
    evidence: str = ""

    def model_post_init(self, __context: Any) -> None:
        self.source_name = self.source_name.strip()
        self.target_name = self.target_name.strip()
        self.relation_type = self.relation_type.strip().lower().replace(" ", "_").replace("-", "_")
        if self.relation_type not in WikiSchema.RELATION_TYPES:
            self.relation_type = "related_to"


class WikiExtraction(BaseModel):
    """LLM 抽取结果容器"""

    entities: list[WikiExtractedEntity] = Field(default_factory=list)
    relations: list[WikiExtractedRelation] = Field(default_factory=list)
