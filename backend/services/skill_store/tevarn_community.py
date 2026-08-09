"""
Tevarn 鑷绀惧尯婧愰€傞厤鍣?

澶嶇敤鍘熸湁鐨?community_skills_index_url 鏈哄埗锛岃緭鍑?UnifiedSkill 鏍煎紡銆?
"""

from __future__ import annotations

import logging

import aiohttp

from backend.core.config import settings
from backend.schemas.skill_store import UnifiedSkill
from backend.services.skill_store.base import SkillStoreFetcher

logger = logging.getLogger(__name__)


_DEFAULT_INDEX_URL = (
    "https://raw.githubusercontent.com/tevarn-ai/community-skills/main/index.json"
)


class TevarnCommunityFetcher(SkillStoreFetcher):
    """Tevarn 瀹樻柟绀惧尯 skill 绱㈠紩閫傞厤鍣?""

    source = "tevarn"
    display_name = "Tevarn Community"

    def __init__(self, index_url: str | None = None):
        self.index_url = (
            index_url
            or getattr(settings, "community_skills_index_url", None)
            or _DEFAULT_INDEX_URL
        )

    async def fetch(self, limit: int = 100) -> list[UnifiedSkill]:
        """鎷夊彇 tevarn 绀惧尯绱㈠紩"""
        if not self.index_url:
            raise RuntimeError("No tevarn community index URL configured")

        async with aiohttp.ClientSession() as session:
            async with session.get(
                self.index_url,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Tevarn index returned {resp.status}")
                data = await resp.json(content_type=None)

        if not isinstance(data, list):
            raise RuntimeError("Tevarn index must be a JSON array")

        skills: list[UnifiedSkill] = []
        for item in data[:limit]:
            try:
                skills.append(self._to_unified(item))
            except Exception as e:
                logger.debug("skip invalid tevarn skill item: %s", e)
                continue
        return skills

    def _to_unified(self, item: dict) -> UnifiedSkill:
        """tevarn 绱㈠紩鍘熷鏁版嵁 鈫?UnifiedSkill"""
        name = item.get("name", "")
        schema = item.get("schema") or item.get("skill_schema") or {}
        
        return UnifiedSkill(
            id=name,
            name=name,
            display_name=name.replace("_", " ").title(),
            summary=item.get("description", "")[:200],
            description=item.get("description", ""),
            source="tevarn",
            source_url="",
            source_repo="tevarn-ai/community-skills",
            skill_md_url="",
            topics=[],
            tags=[],
            compatibility=["tevarn"],
            install_command="",
            raw={"schema": schema, "handler": item.get("handler", "http"), "handler_config": item.get("handler_config", {})},
        )

