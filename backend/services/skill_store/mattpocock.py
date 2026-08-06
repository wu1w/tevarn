"""
mattpocock/skills 源适配器

https://github.com/mattpocock/skills
工程向 Agent Skills（SKILL.md），MIT，兼容 Claude Code / Codex / Takton prompt-skill。

策略：
1. 优先 GitHub Trees API 递归扫描 skills/**/SKILL.md
2. 失败时回退到内置目录（保证商店可用、一键安装可跑）
"""

from __future__ import annotations

import logging
import re

import aiohttp

from backend.schemas.skill_store import SkillPackInfo, SkillStats, UnifiedSkill
from backend.services.skill_store.base import SkillStoreFetcher

logger = logging.getLogger(__name__)

REPO = "mattpocock/skills"
BRANCH = "main"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
TREE_URL = f"https://api.github.com/repos/{REPO}/git/trees/{BRANCH}?recursive=1"
CONTENTS_SKILLS = f"https://api.github.com/repos/{REPO}/contents/skills?ref={BRANCH}"

# 已知目录（API 限流 / 离线时的保底清单，与上游仓库 2026 结构对齐）
_FALLBACK_CATALOG: list[dict[str, str]] = [
    # engineering
    {"id": "ask-matt", "category": "engineering", "summary": "Router to the right engineering skill"},
    {"id": "code-review", "category": "engineering", "summary": "Dual-axis code review (standards + spec)"},
    {"id": "codebase-design", "category": "engineering", "summary": "Deep modules, simple interfaces"},
    {"id": "diagnosing-bugs", "category": "engineering", "summary": "Structured bug diagnosis with feedback loops"},
    {"id": "domain-modeling", "category": "engineering", "summary": "Sharpen domain language; update CONTEXT.md / ADRs"},
    {"id": "grill-with-docs", "category": "engineering", "summary": "Grilling session that builds domain docs"},
    {"id": "implement", "category": "engineering", "summary": "Implement from specs/tickets with TDD + review"},
    {"id": "improve-codebase-architecture", "category": "engineering", "summary": "Scan architecture; output improvement report"},
    {"id": "prototype", "category": "engineering", "summary": "Build prototypes to answer design questions"},
    {"id": "resolving-merge-conflicts", "category": "engineering", "summary": "Resolve git conflicts by intent"},
    {"id": "setup-matt-pocock-skills", "category": "engineering", "summary": "One-time repo setup for issue tracker + docs"},
    {"id": "tdd", "category": "engineering", "summary": "Red-green-refactor test-driven development"},
    {"id": "to-spec", "category": "engineering", "summary": "Turn conversation into a published spec"},
    {"id": "to-tickets", "category": "engineering", "summary": "Break plans into tracer-bullet tickets"},
    {"id": "triage", "category": "engineering", "summary": "Move issues through a labeled state machine"},
    {"id": "wayfinder", "category": "engineering", "summary": "Plan large work via decision tickets"},
    {"id": "research", "category": "engineering", "summary": "Investigate with trusted sources; cited Markdown"},
    {"id": "wizard", "category": "engineering", "summary": "Interactive bash wizards for setup/migration"},
    # productivity
    {"id": "grill-me", "category": "productivity", "summary": "Deep interview to resolve design branches"},
    {"id": "grilling", "category": "productivity", "summary": "Reusable interview logic behind grill skills"},
    {"id": "handoff", "category": "productivity", "summary": "Compact handoff docs for agent continuity"},
    {"id": "teach", "category": "productivity", "summary": "Teach a skill over multi-session work"},
    {"id": "to-questionnaire", "category": "productivity", "summary": "Turn decisions into async questionnaires"},
    {"id": "wait-what", "category": "productivity", "summary": "Rephrase unclear messages in project vocabulary"},
    {"id": "writing-for-agents", "category": "productivity", "summary": "Write agent-readable skills and docs"},
]

# 手机推荐子集（对话向 + 轻量工程，避免重依赖 issue tracker）
MOBILE_PACK_IDS = frozenset({
    "grill-me",
    "handoff",
    "wait-what",
    "research",
    "diagnosing-bugs",
    "tdd",
    "code-review",
    "to-spec",
    "writing-for-agents",
})


def _prettify(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.replace("_", "-").split("-"))


def _skill_from_path(path: str) -> UnifiedSkill | None:
    """skills/<category>/<name>/SKILL.md → UnifiedSkill"""
    m = re.match(r"^skills/([^/]+)/([^/]+)/SKILL\.md$", path)
    if not m:
        return None
    category, name = m.group(1), m.group(2)
    if name.startswith(".") or category in (".", "deprecated", "in-progress", "misc", "personal"):
        return None
    return _make_skill(name, category)


def _make_skill(name: str, category: str, summary: str = "") -> UnifiedSkill:
    skill_md_url = f"{RAW_BASE}/skills/{category}/{name}/SKILL.md"
    return UnifiedSkill(
        id=name,
        name=name.replace("-", "_"),
        display_name=_prettify(name),
        summary=summary or f"Matt Pocock · {category} · {name}",
        description=(
            f"From mattpocock/skills ({category}). "
            "Prompt skill for engineering workflows; injected as SKILL.md."
        ),
        source="mattpocock",
        source_url=f"https://github.com/{REPO}/tree/{BRANCH}/skills/{category}/{name}",
        source_repo=REPO,
        skill_md_url=skill_md_url,
        topics=[category, "mattpocock", "engineering-workflow"],
        tags=[category, "prompt-skill", "mit"],
        license="MIT",
        author="Matt Pocock",
        compatibility=["takton", "claude-code", "codex", "hermes", "openclaw"],
        install_command=f"npx skills@latest add mattpocock/skills --skill={name}",
        stats=SkillStats(),
    )


def list_pack_catalog() -> list[SkillPackInfo]:
    """返回一键技能包目录"""
    all_ids = [c["id"] for c in _FALLBACK_CATALOG]
    eng = [c["id"] for c in _FALLBACK_CATALOG if c["category"] == "engineering"]
    prod = [c["id"] for c in _FALLBACK_CATALOG if c["category"] == "productivity"]
    mobile = [i for i in all_ids if i in MOBILE_PACK_IDS]
    return [
        SkillPackInfo(
            id="mattpocock",
            name="Matt Pocock 完整技能包",
            description="工程 + 效率全套 SKILL.md（grill / tdd / code-review / handoff…）",
            source="mattpocock",
            skill_ids=all_ids,
            count=len(all_ids),
            recommended_for=["pc"],
        ),
        SkillPackInfo(
            id="mattpocock-engineering",
            name="Matt · 工程包",
            description="TDD、评审、实现、架构、工单拆分等工程闭环",
            source="mattpocock",
            skill_ids=eng,
            count=len(eng),
            recommended_for=["pc"],
        ),
        SkillPackInfo(
            id="mattpocock-productivity",
            name="Matt · 效率包",
            description="grill-me、handoff、wait-what 等对话协作 skill",
            source="mattpocock",
            skill_ids=prod,
            count=len(prod),
            recommended_for=["pc", "mobile"],
        ),
        SkillPackInfo(
            id="mattpocock-mobile",
            name="Matt · 手机轻量包",
            description="适合本机 agent 的轻量子集（无 issue tracker 重依赖）",
            source="mattpocock",
            skill_ids=mobile,
            count=len(mobile),
            recommended_for=["mobile", "pc"],
        ),
    ]


def resolve_pack_skill_ids(pack_id: str) -> list[str] | None:
    for p in list_pack_catalog():
        if p.id == pack_id:
            return list(p.skill_ids)
    return None


def lookup_fallback_skill(skill_id: str) -> UnifiedSkill | None:
    """不依赖网络 list 时解析单个 skill"""
    for c in _FALLBACK_CATALOG:
        if c["id"] == skill_id or c["id"].replace("-", "_") == skill_id:
            return _make_skill(c["id"], c["category"], c["summary"])
    return None


class MattPocockFetcher(SkillStoreFetcher):
    """mattpocock/skills GitHub 源"""

    source = "mattpocock"  # type: ignore[assignment]
    display_name = "Matt Pocock Skills"

    async def fetch(self, limit: int = 100) -> list[UnifiedSkill]:
        skills = await self._fetch_from_tree(limit)
        if not skills:
            skills = self._fallback(limit)
        return skills[:limit]

    async def _fetch_from_tree(self, limit: int) -> list[UnifiedSkill]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "Takton-SkillStore/1.0",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    TREE_URL,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=25),
                ) as resp:
                    if resp.status != 200:
                        logger.warning("mattpocock tree API %s", resp.status)
                        return await self._fetch_from_contents(session, headers, limit)
                    data = await resp.json()
        except Exception as e:
            logger.warning("mattpocock tree fetch failed: %s", e)
            return []

        tree = data.get("tree") or []
        skills: list[UnifiedSkill] = []
        seen: set[str] = set()
        for item in tree:
            if item.get("type") != "blob":
                continue
            path = item.get("path") or ""
            if not path.endswith("/SKILL.md"):
                continue
            sk = _skill_from_path(path)
            if not sk or sk.id in seen:
                continue
            seen.add(sk.id)
            for c in _FALLBACK_CATALOG:
                if c["id"] == sk.id:
                    sk.summary = c["summary"]
                    break
            skills.append(sk)
            if len(skills) >= limit:
                break
        return skills

    async def _fetch_from_contents(
        self,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
        limit: int,
    ) -> list[UnifiedSkill]:
        skills: list[UnifiedSkill] = []
        try:
            async with session.get(
                CONTENTS_SKILLS,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    return []
                categories = await resp.json()
        except Exception:
            return []

        for cat in categories:
            if cat.get("type") != "dir":
                continue
            cat_name = cat.get("name") or ""
            if cat_name.startswith(".") or cat_name in (
                "deprecated", "in-progress", "misc", "personal",
            ):
                continue
            url = f"https://api.github.com/repos/{REPO}/contents/skills/{cat_name}?ref={BRANCH}"
            try:
                async with session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
                ) as resp:
                    if resp.status != 200:
                        continue
                    items = await resp.json()
            except Exception:
                continue
            for item in items:
                if len(skills) >= limit:
                    return skills
                if item.get("type") != "dir":
                    continue
                name = item.get("name") or ""
                if name.startswith("."):
                    continue
                skills.append(_make_skill(name, cat_name))
        return skills

    def _fallback(self, limit: int) -> list[UnifiedSkill]:
        out: list[UnifiedSkill] = []
        for c in _FALLBACK_CATALOG:
            if len(out) >= limit:
                break
            out.append(_make_skill(c["id"], c["category"], c["summary"]))
        return out
