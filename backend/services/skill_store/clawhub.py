"""
ClawHub 源适配器

通过 ClawHub 公共 API（https://clawhub.com/api/v1/skills）拉取 openclaw 生态的 skill。
返回的每条 skill 包含 slug/displayName/summary/topics/tags/stats/latestVersion/metadata。
"""

from __future__ import annotations

import logging
from datetime import datetime

import aiohttp

from backend.schemas.skill_store import SkillStats, UnifiedSkill
from backend.services.skill_store.base import SkillStoreFetcher

logger = logging.getLogger(__name__)


class ClawHubFetcher(SkillStoreFetcher):
    """ClawHub (openclaw 社区注册表) 适配器"""

    source = "clawhub"
    display_name = "ClawHub (openclaw)"

    API_BASE = "https://clawhub.com/api/v1/skills"
    DETAIL_BASE = "https://clawhub.com/skills"

    async def fetch(self, limit: int = 100) -> list[UnifiedSkill]:
        """拉取 ClawHub skills 列表"""
        items: list[dict] = []
        cursor: str | None = None
        
        async with aiohttp.ClientSession() as session:
            while len(items) < limit:
                params: dict = {"limit": min(50, limit - len(items))}
                if cursor:
                    params["cursor"] = cursor
                
                async with session.get(
                    self.API_BASE,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"ClawHub API returned {resp.status}")
                    data = await resp.json()
                
                page_items = data.get("items", [])
                if not page_items:
                    break
                items.extend(page_items)
                
                cursor = data.get("nextCursor")
                if not cursor:
                    break
        
        return [self._to_unified(it) for it in items[:limit]]

    async def fetch_by_slug(self, slug: str) -> UnifiedSkill | None:
        """按 slug 查找 skill（从 list 全量搜索，ClawHub 无单个 skill API）
        
        处理歧义：若多个 owner 有同名 slug，返回第一个（通常是最 popular 的）
        """
        # ClawHub 没有 /skills/{slug} 端点，只能从 list 搜索
        # limit=200 足够覆盖（实测 ClawHub 只有 ~140 条）
        skills = await self.fetch(limit=200)
        for s in skills:
            if s.id == slug:
                return s
        return None

    def _to_unified(self, item: dict) -> UnifiedSkill:
        """ClawHub 原始数据 → UnifiedSkill"""
        slug = item.get("slug", "")
        stats_raw = item.get("stats", {}) or {}
        latest = item.get("latestVersion", {}) or {}
        metadata = item.get("metadata", {}) or {}
        
        # 从 tags dict 提取 tag 列表
        tags_dict = item.get("tags", {}) or {}
        tag_list = list(tags_dict.keys()) if isinstance(tags_dict, dict) else []
        
        # setup 环境变量要求 → compatibility hints
        setup = metadata.get("setup", []) or []
        compatibility = ["openclaw", "hermes", "claude-code"]  # SKILL.md 格式通用
        
        # 下载链接：ClawHub 无公开下载 API，标记为 None（前端禁用安装按钮）
        skill_md_url = None
        
        # 容错：API 可能返回 null/None，统一转为空字符串
        def _safe_str(val) -> str:
            return str(val) if val is not None else ""
        
        # source_repo：ClawHub 详情页（用于跳转）
        source_repo = f"{self.DETAIL_BASE}/{slug}"
        
        return UnifiedSkill(
            id=slug,
            name=slug.replace("-", "_").replace("/", "__"),  # takton skill name 只允许 [a-zA-Z0-9_]
            display_name=_safe_str(item.get("displayName") or slug),
            summary=_safe_str(item.get("summary")),
            description=_safe_str(item.get("description")),
            source="clawhub",
            source_url=source_repo,
            source_repo=source_repo,
            skill_md_url=skill_md_url,
            topics=item.get("topics") or [],
            tags=tag_list,
            license=latest.get("license"),
            author="",
            version=latest.get("version", ""),
            stats=SkillStats(
                stars=stats_raw.get("stars", 0),
                downloads=stats_raw.get("downloads", 0),
                installs=stats_raw.get("installs", 0),
                versions=stats_raw.get("versions", 0),
            ),
            install_command=f"openclaw skills install @{slug}",
            compatibility=compatibility,
            created_at=self._ms_to_dt(item.get("createdAt")),
            updated_at=self._ms_to_dt(item.get("updatedAt")),
            raw=item,
        )

    @staticmethod
    def _ms_to_dt(ms: int | None) -> datetime | None:
        """毫秒时间戳 → datetime"""
        if not ms:
            return None
        try:
            return datetime.fromtimestamp(ms / 1000)
        except Exception:
            return None
