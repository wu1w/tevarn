"""
原有 BaseSkill 适配器

将 backend.skills.BaseSkill 子类包装成 BaseTool，
实现 Skill 体系到统一工具体系的过渡。

v3.0 策略：
- 保留 BaseSkill 和 SkillRegistry，但 Agent Loop 只调用 ToolRegistry
- 通过 SkillToolAdapter 把现有 Skill 注册到 ToolRegistry
"""

from __future__ import annotations

from typing import Any

from backend.skills.base import BaseSkill
from backend.tools.base import BaseTool, ToolRiskLevel, ToolSource


class SkillToolAdapter(BaseTool):
    """把现有 BaseSkill 适配成统一 BaseTool"""

    def __init__(self, skill: BaseSkill):
        super().__init__(
            name=skill.name,
            description=skill.description or "",
            parameters=skill.parameters
            or {"type": "object", "properties": {}},
            source=ToolSource.SKILL,
            risk_level=ToolRiskLevel.MEDIUM,
            enabled=True,
            requires_confirmation=False,
        )
        self.skill = skill

    async def execute(self, **kwargs: Any) -> Any:
        return await self.skill.execute(**kwargs)
