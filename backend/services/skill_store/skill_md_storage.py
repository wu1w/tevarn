"""
SKILL.md 下载与本地存储服务

负责：
- 从各源下载 SKILL.md 内容
- 存到本地 ~/.takton/skills/<source>/<name>/SKILL.md
- 提供加载接口供 context pipeline 注入
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import aiohttp

from backend.schemas.skill_store import UnifiedSkill

logger = logging.getLogger(__name__)


# 默认存储路径：~/.takton/skills/
_DEFAULT_SKILLS_ROOT = Path.home() / ".takton" / "skills"


def _skills_root() -> Path:
    """获取 skills 存储根目录（支持环境变量覆盖）"""
    root = os.environ.get("TAKTON_SKILLS_ROOT")
    if root:
        return Path(root)
    return _DEFAULT_SKILLS_ROOT


def _sanitize_name(name: str) -> str:
    """清理 skill 名为合法目录名"""
    return re.sub(r"[^\w\-]", "_", name)


class SkillMdStorage:
    """SKILL.md 本地存储"""

    def __init__(self, root: Path | None = None):
        self.root = root or _skills_root()
        self.root.mkdir(parents=True, exist_ok=True)

    def skill_dir(self, source: str, name: str) -> Path:
        """返回 skill 存储目录"""
        return self.root / source / _sanitize_name(name)

    def skill_md_path(self, source: str, name: str) -> Path:
        """返回 SKILL.md 完整路径"""
        return self.skill_dir(source, name) / "SKILL.md"

    def is_installed(self, source: str, name: str) -> bool:
        """检查是否已安装"""
        return self.skill_md_path(source, name).exists()

    def read(self, source: str, name: str) -> str | None:
        """读取已安装的 SKILL.md 内容"""
        path = self.skill_md_path(source, name)
        if not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to read SKILL.md %s: %s", path, e)
            return None

    def write(self, source: str, name: str, content: str) -> Path:
        """写入 SKILL.md"""
        path = self.skill_md_path(source, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        logger.info("Installed SKILL.md: %s", path)
        return path

    def remove(self, source: str, name: str) -> bool:
        """卸载（删除目录）"""
        skill_dir = self.skill_dir(source, name)
        if not skill_dir.exists():
            return False
        import shutil
        shutil.rmtree(skill_dir)
        logger.info("Uninstalled skill: %s", skill_dir)
        return True

    def list_installed(self) -> list[dict]:
        """列出所有已安装的 prompt-skill"""
        installed: list[dict] = []
        if not self.root.exists():
            return installed
        for source_dir in self.root.iterdir():
            if not source_dir.is_dir():
                continue
            source = source_dir.name
            for skill_dir in source_dir.iterdir():
                if not skill_dir.is_dir():
                    continue
                md_path = skill_dir / "SKILL.md"
                if not md_path.exists():
                    continue
                installed.append({
                    "source": source,
                    "name": skill_dir.name,
                    "path": str(md_path),
                    "size": md_path.stat().st_size,
                })
        return installed


class SkillMdDownloader:
    """SKILL.md 下载器"""

    async def download(self, skill: UnifiedSkill) -> str:
        """下载 skill 的 SKILL.md 内容
        
        优先级：
        1. skill.skill_md_content（已预加载）
        2. skill.skill_md_url（raw 下载链接）
        3. 从 source_repo 探测常见路径
        
        ClawHub 特殊处理：无公开下载 API，抛错提示用 CLI
        """
        if skill.skill_md_content:
            return skill.skill_md_content

        if skill.skill_md_url:
            content = await self._fetch_text(skill.skill_md_url)
            if content:
                return content

        # ClawHub：无公开 SKILL.md 下载时，把元数据转换成可注入的 SKILL.md
        if skill.source == "clawhub":
            return self._convert_clawhub_to_skill_md(skill)

        # 从 GitHub 仓库探测
        if skill.source_repo:
            candidates = self._guess_skill_md_urls(skill)
            for url in candidates:
                content = await self._fetch_text(url)
                if content:
                    return content

        raise RuntimeError(
            f"Cannot download SKILL.md for {skill.name}: no valid URL found"
        )

    @staticmethod
    def _convert_clawhub_to_skill_md(skill: UnifiedSkill) -> str:
        """将 ClawHub 条目转换为 Takton 可用的 SKILL.md（一键转换安装）。

        ClawHub 不提供公开文件下载；用 list API 的元数据生成 frontmatter + 指引正文，
        使小白用户无需 CLI 也能装到本地并注入 system prompt。
        """
        name = (skill.display_name or skill.name or skill.id or "clawhub-skill").strip()
        summary = (skill.summary or skill.description or "").strip()
        description = (skill.description or skill.summary or "").strip()
        topics = skill.topics or []
        tags = skill.tags or []
        tag_line = ", ".join(dict.fromkeys([*topics, *tags]))  # 去重保序
        version = skill.version or ""
        source_url = skill.source_url or skill.source_repo or f"https://clawhub.com/skills/{skill.id}"
        install_cmd = skill.install_command or f"openclaw skills install @{skill.id}"

        # description 行尽量单行，避免 frontmatter 解析歧义
        desc_one_line = re.sub(r"\s+", " ", summary or description or name).strip()
        if len(desc_one_line) > 280:
            desc_one_line = desc_one_line[:280] + "…"

        body_parts = [
            f"# {name}",
            "",
            summary or description or "(无描述)",
            "",
        ]
        if description and description != summary:
            body_parts.extend(["## 详细说明", "", description, ""])

        if topics:
            body_parts.extend(
                [
                    "## Topics",
                    "",
                    ", ".join(topics),
                    "",
                ]
            )

        body_parts.extend(
            [
                "## 来源（ClawHub → Takton 转换）",
                "",
                f"- 源 ID: `clawhub/{skill.id}`",
                f"- 详情页: {source_url}",
            ]
        )
        if version:
            body_parts.append(f"- 版本: `{version}`")
        if skill.stats and (skill.stats.downloads or skill.stats.stars):
            body_parts.append(
                f"- 热度: downloads={skill.stats.downloads}, stars={skill.stats.stars}"
            )
        body_parts.extend(
            [
                "",
                "## 在 Takton 中如何使用",
                "",
                "本 skill 由 ClawHub 元数据**一键转换**为本地 SKILL.md，会注入 system prompt。",
                "当用户请求匹配本 skill 的 description / topics 时：",
                "1. 按上方说明协助用户完成任务；",
                "2. 若需要完整 OpenClaw 运行时实现，可提示用户访问详情页，或使用原生 CLI：",
                f"   `{install_cmd}`",
                "3. 能用现有工具（web/file/bash 等）直接完成的，优先直接做。",
                "",
                "## 兼容提示",
                "",
                "原生态标签: " + ", ".join(skill.compatibility or ["openclaw"]),
                "",
            ]
        )

        fm_lines = [
            "---",
            f"name: {name}",
            f"description: {desc_one_line}",
        ]
        if tag_line:
            fm_lines.append(f"tags: {tag_line}")
        if version:
            fm_lines.append(f"version: {version}")
        fm_lines.extend(
            [
                "source: clawhub",
                f"source_id: {skill.id}",
                f"homepage: {source_url}",
                "converted: true",
                "---",
                "",
            ]
        )
        return "\n".join(fm_lines + body_parts)

    def _guess_skill_md_urls(self, skill: UnifiedSkill) -> list[str]:
        """根据仓库名猜测 SKILL.md 可能的 URL"""
        repo = skill.source_repo
        urls: list[str] = []
        
        # 常见分支
        for branch in ("main", "master"):
            # 根目录
            urls.append(f"https://raw.githubusercontent.com/{repo}/{branch}/SKILL.md")
            # skills/<name>/ 子目录
            urls.append(f"https://raw.githubusercontent.com/{repo}/{branch}/skills/{skill.id}/SKILL.md")
            # <name>/ 子目录（awesome-claude 风格）
            urls.append(f"https://raw.githubusercontent.com/{repo}/{branch}/{skill.id}/SKILL.md")
        
        return urls

    @staticmethod
    async def _fetch_text(url: str) -> str | None:
        """下载文本内容，失败返回 None"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    if resp.status != 200:
                        return None
                    return await resp.text()
        except Exception as e:
            logger.debug("Failed to fetch %s: %s", url, e)
            return None


# 单例
_storage: SkillMdStorage | None = None
_downloader: SkillMdDownloader | None = None


def get_skill_md_storage() -> SkillMdStorage:
    global _storage
    if _storage is None:
        _storage = SkillMdStorage()
    return _storage


def get_skill_md_downloader() -> SkillMdDownloader:
    global _downloader
    if _downloader is None:
        _downloader = SkillMdDownloader()
    return _downloader
