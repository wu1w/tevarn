"""
PC 端默认 prompt-skill 种子（幂等）

启动时写入 ~/.tevarn/skills/<source>/<name>/SKILL.md，
仅在文件不存在时安装（不覆盖用户改过的版本）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from backend.services.skill_store.skill_md_storage import get_skill_md_storage

logger = logging.getLogger(__name__)

# 与 backend/content/default_prompt_skills/ 对齐
_CONTENT_ROOT = Path(__file__).resolve().parents[2] / "content" / "default_prompt_skills"

# (source, skill_id, relative SKILL.md under content root)
_DEFAULT_SKILLS: list[tuple[str, str, str]] = [
    # OpenAI Codex Security — 默认内置
    ("openai", "codex-security", "codex-security/SKILL.md"),
]


def ensure_default_prompt_skills(*, force: bool = False) -> dict:
    """确保默认 prompt-skill 已安装到本地 skill 目录。

    Returns:
        {installed: [...], skipped: [...], errors: [...]}
    """
    storage = get_skill_md_storage()
    installed: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    for source, skill_id, rel in _DEFAULT_SKILLS:
        key = f"{source}/{skill_id}"
        try:
            if storage.is_installed(source, skill_id) and not force:
                skipped.append(key)
                continue
            src_path = _CONTENT_ROOT / rel
            if not src_path.is_file():
                errors.append(f"{key}: missing bundled file {src_path}")
                continue
            content = src_path.read_text(encoding="utf-8")
            if not content.strip():
                errors.append(f"{key}: empty SKILL.md")
                continue
            path = storage.write(source, skill_id, content)
            installed.append(f"{key} -> {path}")
            logger.info("Default prompt-skill installed: %s", key)
        except Exception as e:
            errors.append(f"{key}: {e}")
            logger.warning("Default prompt-skill seed failed %s: %s", key, e)

    return {"installed": installed, "skipped": skipped, "errors": errors}
