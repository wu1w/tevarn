"""
Skill Store 聚合服务

统一入口：
- 并发拉取多个源（带降级）
- 内存缓存（5min TTL）
- 统一搜索 / 过滤 / 排序
- 一键技能包安装（mattpocock 等）
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from backend.schemas.skill_store import (
    InstallPackItemResult,
    InstallPackResponse,
    SkillPackInfo,
    SkillSource,
    SkillStoreQuery,
    SkillStoreResponse,
    UnifiedSkill,
)
from backend.services.skill_store.base import SkillStoreFetcher
from backend.services.skill_store.clawhub import ClawHubFetcher
from backend.services.skill_store.github_awesome import GitHubAwesomeFetcher
from backend.services.skill_store.mattpocock import (
    MattPocockFetcher,
    list_pack_catalog,
    lookup_fallback_skill,
    resolve_pack_skill_ids,
)
from backend.services.skill_store.tevarn_community import TevarnCommunityFetcher

logger = logging.getLogger(__name__)

_CACHE_TTL = 300  # 5 分钟


class SkillStoreService:
    """Skill 商店聚合服务"""

    def __init__(self) -> None:
        self._fetchers: dict[SkillSource, SkillStoreFetcher] = {
            "tevarn": TevarnCommunityFetcher(),
            "clawhub": ClawHubFetcher(),
            "awesome-claude": GitHubAwesomeFetcher("awesome-claude"),
            "awesome-hermes": GitHubAwesomeFetcher("awesome-hermes"),
            "mattpocock": MattPocockFetcher(),
        }
        self._cache: dict[SkillSource, tuple[float, list[UnifiedSkill]]] = {}
        self._cache_lock = asyncio.Lock()

    def register_fetcher(self, fetcher: SkillStoreFetcher) -> None:
        self._fetchers[fetcher.source] = fetcher

    def available_sources(self) -> list[SkillSource]:
        return list(self._fetchers.keys())

    def list_packs(self) -> list[SkillPackInfo]:
        return list_pack_catalog()

    async def list_skills(self, query: SkillStoreQuery) -> SkillStoreResponse:
        sources_to_fetch: list[SkillSource] = (
            [query.source] if query.source else list(self._fetchers.keys())
        )

        tasks = [self._get_cached_or_fetch(src, query.limit) for src in sources_to_fetch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_skills: list[UnifiedSkill] = []
        errors: dict[str, str] = {}
        fetched_sources: list[SkillSource] = []

        for src, result in zip(sources_to_fetch, results, strict=True):
            if isinstance(result, Exception):
                errors[src] = f"{type(result).__name__}: {result}"
                continue
            skills, err = result
            if err:
                errors[src] = err
            if skills:
                fetched_sources.append(src)
                all_skills.extend(skills)

        if query.search:
            kw = query.search.lower()
            all_skills = [
                s
                for s in all_skills
                if kw in s.name.lower()
                or kw in s.display_name.lower()
                or kw in s.summary.lower()
                or kw in s.description.lower()
                or any(kw in t.lower() for t in s.topics)
                or any(kw in t.lower() for t in s.tags)
            ]

        if query.topic:
            t = query.topic.lower()
            all_skills = [
                s
                for s in all_skills
                if any(t == topic.lower() for topic in s.topics)
                or any(t == tag.lower() for tag in s.tags)
            ]

        all_skills.sort(key=lambda s: (-s.stats.downloads, -s.stats.stars, s.name))
        total = len(all_skills)
        page = all_skills[query.offset : query.offset + query.limit]

        return SkillStoreResponse(
            items=page,
            total=total,
            sources=fetched_sources,
            errors=errors,
        )

    async def get_skill(
        self, skill_id: str, source: SkillSource | None = None
    ) -> UnifiedSkill | None:
        if source == "clawhub":
            fetcher = self._fetchers.get("clawhub")
            if isinstance(fetcher, ClawHubFetcher):
                try:
                    return await fetcher.fetch_by_slug(skill_id)
                except Exception as e:
                    logger.warning("ClawHub fetch_by_slug failed for %s: %s", skill_id, e)

        if source == "mattpocock":
            query = SkillStoreQuery(source="mattpocock", search="", limit=200)
            resp = await self.list_skills(query)
            for skill in resp.items:
                if skill.id == skill_id or skill.name == skill_id:
                    return skill
            return lookup_fallback_skill(skill_id)

        query = SkillStoreQuery(source=source, search="", limit=200)
        resp = await self.list_skills(query)
        for skill in resp.items:
            if skill.id == skill_id or skill.name == skill_id:
                return skill

        if source is None:
            return lookup_fallback_skill(skill_id)
        return None

    async def install_pack(
        self,
        pack_id: str,
        *,
        force: bool = False,
    ) -> InstallPackResponse:
        from backend.services.skill_store.skill_md_storage import (
            get_skill_md_downloader,
            get_skill_md_storage,
        )

        skill_ids = resolve_pack_skill_ids(pack_id)
        if not skill_ids:
            return InstallPackResponse(
                success=False,
                pack_id=pack_id,
                message=f"未知技能包: {pack_id}",
            )

        storage = get_skill_md_storage()
        downloader = get_skill_md_downloader()
        items: list[InstallPackItemResult] = []
        installed = failed = skipped = 0

        await self.list_skills(SkillStoreQuery(source="mattpocock", limit=200))

        for sid in skill_ids:
            skill = await self.get_skill(sid, source="mattpocock")
            if not skill:
                skill = lookup_fallback_skill(sid)
            if not skill:
                failed += 1
                items.append(
                    InstallPackItemResult(
                        skill_id=sid, success=False, error="skill not found"
                    )
                )
                continue

            storage_key = skill.id
            if storage.is_installed("mattpocock", storage_key) and not force:
                skipped += 1
                items.append(
                    InstallPackItemResult(
                        skill_id=sid,
                        success=True,
                        path=str(storage.skill_md_path("mattpocock", storage_key)),
                        error="already installed",
                    )
                )
                continue

            try:
                content = await downloader.download(skill)
                path = storage.write("mattpocock", storage_key, content)
                installed += 1
                items.append(
                    InstallPackItemResult(skill_id=sid, success=True, path=str(path))
                )
            except Exception as e:
                alt = await self._download_matt_alt(sid, downloader)
                if alt:
                    try:
                        path = storage.write("mattpocock", storage_key, alt)
                        installed += 1
                        items.append(
                            InstallPackItemResult(
                                skill_id=sid, success=True, path=str(path)
                            )
                        )
                        continue
                    except Exception as e2:
                        e = e2
                failed += 1
                items.append(
                    InstallPackItemResult(skill_id=sid, success=False, error=str(e))
                )

        ok = failed == 0 or installed > 0
        msg = f"技能包 {pack_id}：成功 {installed}，跳过 {skipped}，失败 {failed}"
        return InstallPackResponse(
            success=ok,
            pack_id=pack_id,
            installed=installed,
            failed=failed,
            skipped=skipped,
            items=items,
            message=msg,
        )

    async def _download_matt_alt(self, skill_id: str, downloader: Any) -> str | None:
        from backend.services.skill_store.skill_md_storage import SkillMdDownloader

        if not isinstance(downloader, SkillMdDownloader):
            return None
        slug = skill_id.replace("_", "-")
        for cat in ("engineering", "productivity"):
            url = (
                f"https://raw.githubusercontent.com/mattpocock/skills/main/"
                f"skills/{cat}/{slug}/SKILL.md"
            )
            text = await downloader._fetch_text(url)
            if text and ("name:" in text[:800] or text.lstrip().startswith("#")):
                return text
        return None

    async def invalidate_cache(self, source: SkillSource | None = None) -> None:
        async with self._cache_lock:
            if source:
                self._cache.pop(source, None)
            else:
                self._cache.clear()

    async def _get_cached_or_fetch(
        self, source: SkillSource, limit: int
    ) -> tuple[list[UnifiedSkill], str | None]:
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


_service: SkillStoreService | None = None


def get_skill_store_service() -> SkillStoreService:
    global _service
    if _service is None:
        _service = SkillStoreService()
    return _service
