"""
Skill 抽象基类
所有 Agent 技能需继承此类，实现 execute 方法
"""

from abc import ABC, abstractmethod
from typing import Any


class BaseSkill(ABC):
    """
    Skill 抽象基类

    每个 Skill 对应一个 LLM 可调用的工具：
    - name: 工具名称（LLM 通过此名称调用）
    - description: 工具功能描述（影响 LLM 的选择）
    - parameters: JSON Schema 格式的参数定义
    """

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # 自动注册子类到 Registry
        from .registry import SkillRegistry

        if cls.name:
            SkillRegistry.register(cls)

    @abstractmethod
    async def execute(self, **kwargs) -> str:
        """
        执行 Skill

        Args:
            **kwargs: LLM 传入的参数

        Returns:
            执行结果的字符串表示，会被追加到 messages 中作为 tool 角色
        """
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
