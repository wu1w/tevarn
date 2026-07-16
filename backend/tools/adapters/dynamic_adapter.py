"""
动态 Skill 适配器

backend.skills.dynamic.DynamicSkill 允许用户通过 YAML 配置自定义技能。
这里把它包装成 BaseTool 接入统一工具注册表。
"""

from __future__ import annotations

from typing import Any

from backend.skills.dynamic import DynamicSkill
from backend.tools.base import BaseTool, ToolRiskLevel, ToolSource


class DynamicSkillAdapter(BaseTool):
    """将用户自定义 DynamicSkill 适配为统一 BaseTool"""

    def __init__(self, skill: DynamicSkill):
        super().__init__(
            name=skill.name,
            description=skill.description or "",
            parameters=skill.parameters
            or {"type": "object", "properties": {}},
            source=ToolSource.DYNAMIC,
            risk_level=ToolRiskLevel.MEDIUM,
            enabled=True,
            requires_confirmation=False,
        )
        self.skill = skill

    async def execute(self, **kwargs: Any) -> Any:
        return await self.skill.execute(**kwargs)
