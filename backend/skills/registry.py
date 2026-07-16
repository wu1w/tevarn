"""
Skill 注册表
管理所有可用 Skill 的注册与发现
"""

import logging
from typing import Any, Type

from .base import BaseSkill

logger = logging.getLogger(__name__)


class SkillRegistry:
    """Skill 注册表"""

    _skills: dict[str, Type[BaseSkill]] = {}
    _instances: dict[str, BaseSkill] = {}

    @classmethod
    def register(cls, skill_class: Type[BaseSkill]) -> None:
        """注册 Skill 类"""
        if not skill_class.name:
            logger.warning(f"Skill {skill_class.__name__} has no name, skipping registration")
            return
        cls._skills[skill_class.name] = skill_class
        logger.info(f"Registered skill: {skill_class.name}")

    @classmethod
    def get_skill(cls, name: str) -> BaseSkill | None:
        """获取 Skill 实例（懒加载）"""
        if name not in cls._instances:
            skill_class = cls._skills.get(name)
            if skill_class is None:
                return None
            cls._instances[name] = skill_class()
        return cls._instances[name]

    @classmethod
    def get_all_skills(cls) -> list[BaseSkill]:
        """获取所有已注册的 Skill 实例"""
        return [
            cls.get_skill(name)
            for name in cls._skills
            if cls.get_skill(name) is not None
        ]

    @classmethod
    def get_active_skills(cls, enabled_names: list[str] | None = None) -> list[BaseSkill]:
        """
        获取当前启用的 Skill 列表

        Args:
            enabled_names: 启用的 Skill 名称列表，None 表示全部启用
        """
        all_skills = cls.get_all_skills()
        if enabled_names is None:
            return all_skills
        return [s for s in all_skills if s.name in enabled_names]

    @classmethod
    def get_tools_schema(cls, enabled_names: list[str] | None = None) -> list[dict[str, Any]]:
        """获取启用的 Skill 的 JSON Schema 列表（供 LLM 使用）"""
        skills = cls.get_active_skills(enabled_names)
        return [s.to_json_schema() for s in skills]

    @classmethod
    def clear(cls) -> None:
        """清空注册表（主要用于测试）"""
        cls._skills.clear()
        cls._instances.clear()
