"""
GitHub Awesome-List 源适配器

解析 awesome-claude-skills / awesome-hermes-skills 等仓库的目录结构，
提取每个 skill 文件夹作为一条 UnifiedSkill 记录。

策略：
- 调用 GitHub API 列出仓库根目录（排除非 skill 目录）
- 对每个目录尝试访问 `<dir>/SKILL.md`，若存在则认为是有效 skill
- 从 SKILL.md 的 frontmatter 提取 name/description（可选优化）
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Any

import aiohttp

from backend.schemas.skill_store import UnifiedSkill
from backend.services.skill_store.base import SkillStoreFetcher

logger = logging.getLogger(__name__)


# 预置的 awesome 仓库配置
AWESOME_REPOS: dict[str, dict[str, Any]] = {
    "awesome-claude": {
        "repo": "ComposioHQ/awesome-claude-skills",
        "branch": "master",
        "display_name": "Awesome Claude Skills",
        "exclude_dirs": {
            ".github", "composio-skills", "connect-apps-plugin",
            "connect-apps", "document-skills",  # 这些是 plugin 集合而非单 skill
        },
        "compatibility": ["claude-code", "hermes", "openclaw", "takton"],
    },
    "awesome-hermes": {
        "repo": "ZeroPointRepo/awesome-hermes-skills",
        "branch": "main",
        "display_name": "Awesome Hermes Skills",
        # hermes awesome list 是 README 单文件索引，不是目录结构，特殊处理
        "readme_index": True,
        "compatibility": ["hermes", "claude-code", "openclaw", "takton"],
    },
}


class GitHubAwesomeFetcher(SkillStoreFetcher):
    """GitHub awesome-* 仓库适配器（目录结构解析）"""

    def __init__(self, source_key: str):
        """初始化指定 awesome 仓库
        
        Args:
            source_key: "awesome-claude" 或 "awesome-hermes"
        """
        if source_key not in AWESOME_REPOS:
            raise ValueError(f"Unknown awesome source: {source_key}")
        self.source = source_key  # type: ignore
        self.config = AWESOME_REPOS[source_key]
        self.display_name = self.config["display_name"]
        self.repo = self.config["repo"]
        self.branch = self.config["branch"]

    async def fetch(self, limit: int = 100) -> list[UnifiedSkill]:
        """拉取 awesome 仓库的 skill 列表"""
        if self.config.get("readme_index"):
            return await self._fetch_from_readme(limit)
        return await self._fetch_from_directory(limit)

    async def _fetch_from_directory(self, limit: int) -> list[UnifiedSkill]:
        """从目录结构提取 skills（适用于 awesome-claude-skills）"""
        url = f"https://api.github.com/repos/{self.repo}/contents"
        headers = {"Accept": "application/vnd.github+json"}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=headers,
                params={"ref": self.branch},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"GitHub API returned {resp.status} for {self.repo}")
                items = await resp.json()
        
        exclude = self.config.get("exclude_dirs", set())
        skills: list[UnifiedSkill] = []
        
        for item in items:
            if len(skills) >= limit:
                break
            if item.get("type") != "dir":
                continue
            name = item.get("name", "")
            if name.startswith(".") or name in exclude:
                continue
            
            # 构造 UnifiedSkill
            skill_name = name.replace("-", "_")
            skills.append(UnifiedSkill(
                id=name,
                name=skill_name,
                display_name=self._prettify_name(name),
                summary="",
                description="",
                source=self.source,  # type: ignore
                source_url=f"https://github.com/{self.repo}/tree/{self.branch}/{name}",
                source_repo=self.repo,
                skill_md_url=f"https://raw.githubusercontent.com/{self.repo}/{self.branch}/{name}/SKILL.md",
                topics=[],
                tags=[],
                compatibility=self.config.get("compatibility", []),
                install_command=f"git clone https://github.com/{self.repo} && cp -r {name} ~/.claude/skills/",
            ))
        
        return skills

    async def _fetch_from_readme(self, limit: int) -> list[UnifiedSkill]:
        """从 README 提取 skills（适用于 awesome-hermes-skills 这种单文件索引）"""
        url = f"https://raw.githubusercontent.com/{self.repo}/{self.branch}/README.md"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Failed to fetch README from {self.repo}")
                content = await resp.text()
        
        # 提取 markdown 链接 [name](repo-url) — 格式
        # 匹配 `- [name](https://github.com/owner/repo) by [author](url) — description` 模式
        pattern = re.compile(
            r"^\s*[-*]\s*\[([^\]]+)\]\((https://github\.com/[^)]+)\)\s*(?:by\s*\[([^\]]+)\]\([^)]+\))?\s*[—\-–]\s*(.+?)(?:\s*\*\*([^*]+)\*\*)?$",
            re.MULTILINE,
        )
        
        skills: list[UnifiedSkill] = []
        seen = set()
        
        for match in pattern.finditer(content):
            if len(skills) >= limit:
                break
            name, repo_url, author, desc, tag = match.groups()
            
            # 从 repo_url 提取 owner/repo
            m = re.match(r"https://github\.com/([^/]+/[^/]+)", repo_url)
            if not m:
                continue
            skill_repo = m.group(1).rstrip("/")
            
            # 跳过主仓库本身的引用
            if skill_repo.lower() == self.repo.lower():
                continue
            
            skill_id = name.lower().replace(" ", "-").replace("_", "-")
            if skill_id in seen:
                continue
            seen.add(skill_id)
            
            skills.append(UnifiedSkill(
                id=skill_id,
                name=skill_id.replace("-", "_"),
                display_name=name,
                summary=(desc or "").strip()[:200],
                description="",
                source=self.source,  # type: ignore
                source_url=repo_url,
                source_repo=skill_repo,
                skill_md_url="",  # 需要进一步探测 SKILL.md 位置
                topics=[tag] if tag else [],
                tags=[],
                author=author or "",
                compatibility=self.config.get("compatibility", []),
                install_command=f"hermes skills install {skill_repo}",
            ))
        
        return skills

    @staticmethod
    def _prettify_name(slug: str) -> str:
        """kebab-case → Title Case"""
        return " ".join(w.capitalize() for w in slug.split("-"))
