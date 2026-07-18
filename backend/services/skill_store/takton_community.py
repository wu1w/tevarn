"""
Takton 自家社区源适配器

复用原有的 community_skills_index_url 机制，输出 UnifiedSkill 格式。
"""

from __future__ import annotations

import logging

import aiohttp

from backend.core.config import settings
from backend.schemas.skill_store import UnifiedSkill
from backend.services.skill_store.base import SkillStoreFetcher

logger = logging.getLogger(__name__)


_DEFAULT_INDEX_URL = (
    "https://raw.githubusercontent.com/takton-ai/community-skills/main/index.json"
)


class TaktonCommunityFetcher(SkillStoreFetcher):
    """Takton 官方社区 skill 索引适配器"""

    source = "takton"
    display_name = "Takton Community"

    def __init__(self, index_url: str | None = None):
        self.index_url = (
            index_url
            or getattr(settings, "community_skills_index_url", None)
            or _DEFAULT_INDEX_URL
        )

    async def fetch(self, limit: int = 100) -> list[UnifiedSkill]:
        """拉取 takton 社区索引"""
        if not self.index_url:
            raise RuntimeError("No takton community index URL configured")

        async with aiohttp.ClientSession() as session:
            async with session.get(
                self.index_url,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Takton index returned {resp.status}")
                data = await resp.json(content_type=None)

        if not isinstance(data, list):
            raise RuntimeError("Takton index must be a JSON array")

        skills: list[UnifiedSkill] = []
        for item in data[:limit]:
            try:
                skills.append(self._to_unified(item))
            except Exception as e:
                logger.debug("skip invalid takton skill item: %s", e)
                continue
        return skills

    def _to_unified(self, item: dict) -> UnifiedSkill:
        """takton 索引原始数据 → UnifiedSkill"""
        name = item.get("name", "")
        schema = item.get("schema") or item.get("skill_schema") or {}
        
        return UnifiedSkill(
            id=name,
            name=name,
            display_name=name.replace("_", " ").title(),
            summary=item.get("description", "")[:200],
            description=item.get("description", ""),
            source="takton",
            source_url="",
            source_repo="takton-ai/community-skills",
            skill_md_url="",
            topics=[],
            tags=[],
            compatibility=["takton"],
            install_command="",
            raw={"schema": schema, "handler": item.get("handler", "http"), "handler_config": item.get("handler_config", {})},
        )
