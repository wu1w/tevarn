"""
Skill Store 聚合服务

统一入口：
- 并发拉取多个源（带降级）
- 内存缓存（5min TTL）
- 统一搜索 / 过滤 / 排序
"""

from __future__ import annotations

import asyncio
import logging
import time

from backend.schemas.skill_store import (
    SkillSource,
    SkillStoreQuery,
    SkillStoreResponse,
    UnifiedSkill,
)
from backend.services.skill_store.base import SkillStoreFetcher
from backend.services.skill_store.clawhub import ClawHubFetcher
from backend.services.skill_store.github_awesome import GitHubAwesomeFetcher
from backend.services.skill_store.takton_community import TaktonCommunityFetcher

logger = logging.getLogger(__name__)


_CACHE_TTL = 300  # 5 分钟


class SkillStoreService:
    """Skill 商店聚合服务"""

    def __init__(self) -> None:
        self._fetchers: dict[SkillSource, SkillStoreFetcher] = {
            "takton": TaktonCommunityFetcher(),
            "clawhub": ClawHubFetcher(),
            "awesome-claude": GitHubAwesomeFetcher("awesome-claude"),
            "awesome-hermes": GitHubAwesomeFetcher("awesome-hermes"),
        }
        # 缓存：source -> (timestamp, skills)
        self._cache: dict[SkillSource, tuple[float, list[UnifiedSkill]]] = {}
        self._cache_lock = asyncio.Lock()

    def register_fetcher(self, fetcher: SkillStoreFetcher) -> None:
        """注册新源"""
        self._fetchers[fetcher.source] = fetcher

    def available_sources(self) -> list[SkillSource]:
        """返回当前启用的源"""
        return list(self._fetchers.keys())

    async def list_skills(self, query: SkillStoreQuery) -> SkillStoreResponse:
        """列出 skills（聚合多源 + 缓存 + 过滤）"""
        sources_to_fetch: list[SkillSource] = (
            [query.source] if query.source else list(self._fetchers.keys())
        )

        # 并发拉取（带缓存）
        tasks = [self._get_cached_or_fetch(src, query.limit) for src in sources_to_fetch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_skills: list[UnifiedSkill] = []
        errors: dict[str, str] = {}
        fetched_sources: list[SkillSource] = []

        for src, result in zip(sources_to_fetch, results):
            if isinstance(result, Exception):
                errors[src] = f"{type(result).__name__}: {result}"
                continue
            skills, err = result
            if err:
                errors[src] = err
            if skills:
                fetched_sources.append(src)
                all_skills.extend(skills)

        # 搜索过滤
        if query.search:
            kw = query.search.lower()
            all_skills = [
                s for s in all_skills
                if kw in s.name.lower()
                or kw in s.display_name.lower()
                or kw in s.summary.lower()
                or kw in s.description.lower()
                or any(kw in t.lower() for t in s.topics)
                or any(kw in t.lower() for t in s.tags)
            ]

        # topic 过滤
        if query.topic:
            t = query.topic.lower()
            all_skills = [
                s for s in all_skills
                if any(t == topic.lower() for topic in s.topics)
                or any(t == tag.lower() for tag in s.tags)
            ]

        # 排序：downloads desc, stars desc, name asc
        all_skills.sort(key=lambda s: (-s.stats.downloads, -s.stats.stars, s.name))

        total = len(all_skills)
        # 分页
        page = all_skills[query.offset : query.offset + query.limit]

        return SkillStoreResponse(
            items=page,
            total=total,
            sources=fetched_sources,
            errors=errors,
        )

    async def get_skill(self, skill_id: str, source: SkillSource | None = None) -> UnifiedSkill | None:
        """按 id 查找 skill（跨源搜索）
        
        优先策略：
        1. 若 source=clawhub，直接调 ClawHub API /skills/{slug}（绕过 list limit）
        2. 否则用 list_skills 搜索（limit=200 覆盖大多数场景）
        """
        # 优先：ClawHub 直接查询
        if source == "clawhub":
            fetcher = self._fetchers.get("clawhub")
            if isinstance(fetcher, ClawHubFetcher):
                try:
                    return await fetcher.fetch_by_slug(skill_id)
                except Exception as e:
                    logger.warning("ClawHub fetch_by_slug failed for %s: %s", skill_id, e)
        
        # 降级：list 搜索
        query = SkillStoreQuery(source=source, search="", limit=200)
        resp = await self.list_skills(query)
        for skill in resp.items:
            if skill.id == skill_id:
                return skill
        return None

    async def invalidate_cache(self, source: SkillSource | None = None) -> None:
        """失效缓存"""
        async with self._cache_lock:
            if source:
                self._cache.pop(source, None)
            else:
                self._cache.clear()

    async def _get_cached_or_fetch(
        self, source: SkillSource, limit: int
    ) -> tuple[list[UnifiedSkill], str | None]:
        """带缓存的 fetch"""
        async with self._cache_lock:
            cached = self._cache.get(source)
            if cached:
                ts, skills = cached
                if time.time() - ts < _CACHE_TTL:
                    return skills, None

        fetcher = self._fetchers.get(source)
        if not fetcher:
            return [], f"Unknown source: {source}"

        skills, err = await fetcher.fetch_safe(limit=limit)

        if skills:
            async with self._cache_lock:
                self._cache[source] = (time.time(), skills)

        return skills, err


# 全局单例
_service: SkillStoreService | None = None


def get_skill_store_service() -> SkillStoreService:
    """获取 SkillStoreService 单例"""
    global _service
    if _service is None:
        _service = SkillStoreService()
    return _service
