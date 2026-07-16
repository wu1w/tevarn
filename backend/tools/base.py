"""
统一工具抽象层（Takton Tool v3.0）

所有 Agent 可调用的能力都通过 BaseTool 抽象：
- 内置工具（builtin）
- 原有 Skill（skill 适配器）
- 用户自定义 Skill（dynamic）
- 数据库 Tool（db 适配器）
- MCP 工具（mcp 适配器，见 backend/tools/adapters/mcp_adapter.py）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any


class ToolSource(str, Enum):
    """工具来源"""

    BUILTIN = "builtin"
    SKILL = "skill"
    DYNAMIC = "dynamic"
    DB = "db"
    MCP = "mcp"


class ToolRiskLevel(str, Enum):
    """工具风险等级"""

    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    DANGEROUS = "dangerous"

    @classmethod
    def map(cls, value: str | None) -> "ToolRiskLevel":
        if not value:
            return cls.MEDIUM
        try:
            return cls(value.lower())
        except ValueError:
            return cls.MEDIUM


class BaseTool(ABC):
    """
    统一工具抽象基类

    所有可被 Agent 调用的能力都通过继承此类实现。
    """

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        source: ToolSource = ToolSource.BUILTIN,
        risk_level: ToolRiskLevel = ToolRiskLevel.MEDIUM,
        enabled: bool = True,
        requires_confirmation: bool = False,
        allowed_paths: list[str] | None = None,
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.source = source
        self.risk_level = risk_level
        self.enabled = enabled
        self.requires_confirmation = requires_confirmation
        self.allowed_paths = allowed_paths

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """执行工具"""
        raise NotImplementedError

    def to_json_schema(self) -> dict[str, Any]:
        """转换为 LLM 使用的工具定义格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"name={self.name!r}, source={self.source.value}, enabled={self.enabled})"
        )
