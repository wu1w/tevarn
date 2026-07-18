"""
Skill Store Fetcher 基类

每个生态源一个 fetcher，统一输出 list[UnifiedSkill]。
支持失败降级：单源失败不影响其他源。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from backend.schemas.skill_store import SkillSource, UnifiedSkill

logger = logging.getLogger(__name__)


class SkillStoreFetcher(ABC):
    """Skill 商店源适配器基类"""

    source: SkillSource = "custom"
    display_name: str = "Custom"
    default_enabled: bool = True

    @abstractmethod
    async def fetch(self, limit: int = 100) -> list[UnifiedSkill]:
        """拉取该源的 skill 列表
        
        Args:
            limit: 最大返回条数
        
        Returns:
            UnifiedSkill 列表
        
        Raises:
            Exception: 拉取失败时抛出，由上层 catch 并降级
        """
        ...

    async def fetch_safe(self, limit: int = 100) -> tuple[list[UnifiedSkill], str | None]:
        """带降级包装的 fetch
        
        Returns:
            (skills, error_message)
            成功时 error_message=None，失败时 skills=[]
        """
        try:
            skills = await self.fetch(limit=limit)
            return skills, None
        except Exception as e:
            logger.warning("Skill store fetcher %s failed: %s", self.source, e)
            return [], f"{type(e).__name__}: {e}"

    def matches_search(self, skill: UnifiedSkill, keyword: str) -> bool:
        """统一的搜索匹配逻辑"""
        if not keyword:
            return True
        kw = keyword.lower()
        return (
            kw in skill.name.lower()
            or kw in skill.display_name.lower()
            or kw in skill.summary.lower()
            or kw in skill.description.lower()
            or any(kw in t.lower() for t in skill.topics)
            or any(kw in t.lower() for t in skill.tags)
        )
