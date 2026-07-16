"""Takton Package 规范 — 统一 skill / sub_agent / workflow 的可挂载包。

设计原则（对齐 Pi harness）：
- 核心 loop 保持精简；能力通过 package 按需挂到会话
- 包可携带 system_snippet（注入 Context 层），不污染 Stable 核心
- 支持从 workspace/packages 目录加载，也可从现有 skill/子代理/工作流“虚拟投影”
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

PackageType = Literal["skill", "sub_agent", "workflow", "mixed", "bundle"]


class PackageManifest(BaseModel):
    """package.json / PACKAGE.yaml 对应的规范化清单。"""

    name: str = Field(..., description="包唯一名（目录名或显式 name）")
    version: str = Field(default="0.1.0")
    type: PackageType = Field(default="bundle")
    description: str = Field(default="")
    icon: str = Field(default="📦")
    author: str = Field(default="")

    # 挂载到会话后注入 Context 层的系统片段（短）
    system_snippet: str = Field(default="")

    # 建议启用的工具名（提示层；真正过滤可后续接 toolset）
    tools: list[str] = Field(default_factory=list)

    # 引用已有资源（不强制内联完整 DAG/代码）
    skill_names: list[str] = Field(default_factory=list)
    sub_agent_ids: list[str] = Field(default_factory=list)
    workflow_ids: list[str] = Field(default_factory=list)

    # 相对包目录的可选资源
    skill_paths: list[str] = Field(default_factory=list)
    workflow_paths: list[str] = Field(default_factory=list)

    enabled_by_default: bool = False
    tags: list[str] = Field(default_factory=list)

    # 来源元数据（loader 填充）
    source: str = Field(default="workspace")  # workspace | skill | sub_agent | workflow
    path: str = Field(default="")
    virtual: bool = Field(default=False)


class PackageListItem(BaseModel):
    name: str
    version: str = "0.1.0"
    type: PackageType = "bundle"
    description: str = ""
    icon: str = "📦"
    source: str = "workspace"
    virtual: bool = False
    path: str = ""
    system_snippet_preview: str = ""
    tools: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    attached: bool = False


class PackageDetail(PackageListItem):
    system_snippet: str = ""
    skill_names: list[str] = Field(default_factory=list)
    sub_agent_ids: list[str] = Field(default_factory=list)
    workflow_ids: list[str] = Field(default_factory=list)
    manifest: dict[str, Any] = Field(default_factory=dict)


class SessionPackagesState(BaseModel):
    session_id: str
    attached: list[str] = Field(default_factory=list)
    snippets: list[dict[str, str]] = Field(default_factory=list)
