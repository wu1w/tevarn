"""
Prompt-Skill 加载与注入策略

负责：
- 扫描 ~/.takton/skills/ 下已安装的 SKILL.md
- 解析 frontmatter（name/description）+ 正文
- 按策略输出 system prompt 可注入的片段

注入策略（prompt_skill_mode）：
- summary：只注入「目录摘要 + Path」（最省 token）
- auto（默认）：目录摘要 + 与当前 user_input 相关的 skill 注入全文（限额）
- full：尽量注入全文（仍受 max_full / full_max_chars 约束）

设计原则：
- 注入 Context 层（不污染 Stable 身份）
- 相关全文按需，避免把所有 SKILL.md 塞爆 context
- 无相关命中时回退为摘要，并提示可用 file_read 读 Path
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from backend.services.skill_store.skill_md_storage import get_skill_md_storage

logger = logging.getLogger(__name__)

PromptSkillMode = Literal["summary", "auto", "full"]

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
# 英文/数字 token；中日韩连续字串也会单独作为 token
_TOKEN_RE = re.compile(
    r"[a-zA-Z][a-zA-Z0-9_\-]{1,}|[0-9]+|[\u4e00-\u9fff]{1,8}",
    re.UNICODE,
)
# 低信息量停用词（中英）
_STOP = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "is", "are",
    "this", "that", "it", "be", "as", "by", "from", "at", "use", "using", "when",
    "please", "help", "how", "what", "can", "you", "i", "me", "my",
    "的", "了", "吗", "呢", "啊", "吧", "是", "在", "有", "和", "与", "或", "对",
    "一下", "一个", "这个", "那个", "什么", "怎么", "如何", "请", "帮我", "帮忙",
}


@dataclass
class PromptSkill:
    """已安装的 prompt-skill"""

    source: str
    name: str
    display_name: str
    description: str
    body: str
    full_content: str
    path: str
    size: int
    tags: list[str] = field(default_factory=list)


@dataclass
class SkillMatch:
    skill: PromptSkill
    score: float
    reasons: list[str]


@dataclass
class InjectionPlan:
    """一次注入计划（便于日志 / API 调试）"""

    mode: str
    summary_skills: list[str]
    full_skills: list[str]
    scores: dict[str, float]
    block_chars: int = 0


def _parse_frontmatter(content: str) -> tuple[dict[str, str], str]:
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}, content

    fm_text = m.group(1)
    body = content[m.end() :]

    meta: dict[str, str] = {}
    current_key: str | None = None
    current_value_lines: list[str] = []

    def _flush() -> None:
        if current_key:
            meta[current_key] = "\n".join(current_value_lines).strip()

    for line in fm_text.split("\n"):
        kv = re.match(r"^([A-Za-z_][\w\-]*)\s*:\s*(.*)$", line)
        if kv:
            _flush()
            current_key = kv.group(1)
            current_value_lines = [kv.group(2)]
        elif current_key and line.startswith((" ", "\t")):
            current_value_lines.append(line.strip())
        elif current_key and not line.strip():
            _flush()
            current_key = None
            current_value_lines = []
    _flush()
    return meta, body


def _tokenize(text: str) -> set[str]:
    if not text:
        return set()
    out: set[str] = set()
    for t in _TOKEN_RE.findall(text.lower()):
        t = t.strip("-_")
        if len(t) < 2 and not ("\u4e00" <= t <= "\u9fff"):
            continue
        if t in _STOP:
            continue
        out.add(t)
        # 中文 2-gram 提升「构建服务器」类短语命中
        if all("\u4e00" <= c <= "\u9fff" for c in t) and len(t) >= 2:
            for i in range(len(t) - 1):
                out.add(t[i : i + 2])
    return out


def _parse_tags(raw: str) -> list[str]:
    if not raw:
        return []
    # 支持 "a, b" / "[a, b]" / "a b"
    s = raw.strip().strip("[]")
    parts = re.split(r"[,|/\s]+", s)
    return [p.strip().strip("\"'") for p in parts if p.strip().strip("\"'")]


class PromptSkillLoader:
    """Prompt-Skill 加载器 + 注入策略"""

    def list_installed(self) -> list[PromptSkill]:
        storage = get_skill_md_storage()
        raw_list = storage.list_installed()
        skills: list[PromptSkill] = []

        for item in raw_list:
            source = item["source"]
            name = item["name"]
            content = storage.read(source, name)
            if not content:
                continue

            meta, body = _parse_frontmatter(content)
            display_name = meta.get("name") or name
            description = meta.get("description") or ""
            tags = _parse_tags(meta.get("tags") or meta.get("topics") or "")

            skills.append(
                PromptSkill(
                    source=source,
                    name=name,
                    display_name=display_name,
                    description=description,
                    body=body,
                    full_content=content,
                    path=item["path"],
                    size=item["size"],
                    tags=tags,
                )
            )
        return skills

    def get_skill(self, source: str, name: str) -> PromptSkill | None:
        for skill in self.list_installed():
            if skill.source == source and skill.name == name:
                return skill
        return None

    def score_relevance(self, skill: PromptSkill, user_input: str) -> SkillMatch:
        """计算 skill 与当前用户输入的相关度 (0~1+ 粗分)。"""
        q = (user_input or "").strip()
        if not q:
            return SkillMatch(skill=skill, score=0.0, reasons=[])

        q_lower = q.lower()
        q_tokens = _tokenize(q)
        reasons: list[str] = []
        score = 0.0

        # 1) 显式点名：skill id / display_name / source/name
        id_keys = {
            skill.name.lower(),
            skill.display_name.lower(),
            f"{skill.source}/{skill.name}".lower(),
            skill.name.lower().replace("-", " "),
            skill.display_name.lower().replace("-", " "),
        }
        for k in id_keys:
            if k and len(k) >= 2 and k in q_lower:
                score += 3.0
                reasons.append(f"name:{k}")
                break

        # 2) description / tags token 重叠
        desc_tokens = _tokenize(skill.description)
        name_tokens = _tokenize(f"{skill.display_name} {skill.name}")
        tag_tokens = _tokenize(" ".join(skill.tags))
        body_head = skill.body[:1500] if skill.body else ""
        body_tokens = _tokenize(body_head)

        if q_tokens:
            for label, pool, weight in (
                ("name", name_tokens, 1.2),
                ("desc", desc_tokens, 1.0),
                ("tag", tag_tokens, 0.9),
                ("body", body_tokens, 0.35),
            ):
                if not pool:
                    continue
                inter = q_tokens & pool
                if not inter:
                    continue
                # Jaccard-ish but 对 query 侧加权
                hit = len(inter) / max(1, len(q_tokens))
                score += hit * weight * 2.0
                if hit >= 0.15 or len(inter) >= 2:
                    reasons.append(f"{label}:{','.join(sorted(list(inter))[:5])}")

        # 3) 中英文关键子串（长度>=3 或中文>=2）直接加分
        needles = [
            skill.name,
            skill.display_name,
            *[t for t in skill.tags if len(t) >= 2],
        ]
        # description 里的关键词片段
        for m in re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{2,6}", skill.description or ""):
            if m.lower() not in _STOP:
                needles.append(m)
        seen: set[str] = set()
        for n in needles:
            nl = n.lower().strip()
            if not nl or nl in seen:
                continue
            seen.add(nl)
            if len(nl) < 2:
                continue
            if nl in q_lower:
                score += 0.55 if len(nl) < 4 else 0.9
                reasons.append(f"substr:{nl[:24]}")

        # 归一到大约 0~1 便于阈值；保留 >1 表示强命中
        return SkillMatch(skill=skill, score=score, reasons=reasons[:8])

    def select_full_skills(
        self,
        skills: list[PromptSkill],
        user_input: str,
        *,
        mode: PromptSkillMode = "auto",
        max_full: int = 2,
        threshold: float = 0.85,
    ) -> list[SkillMatch]:
        """决定哪些 skill 注入全文。"""
        if not skills or max_full <= 0:
            return []

        if mode == "summary":
            return []

        if mode == "full":
            # 仍按 size 小的优先，避免一上来塞最大的
            ordered = sorted(skills, key=lambda s: s.size)
            return [SkillMatch(skill=s, score=99.0, reasons=["mode:full"]) for s in ordered[:max_full]]

        # auto：打分排序
        matches = [self.score_relevance(s, user_input) for s in skills]
        matches.sort(key=lambda m: m.score, reverse=True)

        selected: list[SkillMatch] = []
        for m in matches:
            if m.score < threshold:
                continue
            selected.append(m)
            if len(selected) >= max_full:
                break

        # 若最高分接近阈值但没过：取 Top1 且 score>= threshold*0.6 作为弱相关全文
        if not selected and matches and matches[0].score >= max(0.4, threshold * 0.55):
            selected = [matches[0]]
            selected[0].reasons.append("weak-top1")

        return selected

    def build_summary_block(
        self,
        skills: list[PromptSkill] | None = None,
        *,
        full_ids: set[str] | None = None,
    ) -> str:
        if skills is None:
            skills = self.list_installed()
        if not skills:
            return ""

        full_ids = full_ids or set()
        lines = [
            "# Installed Prompt Skills",
            "",
            "以下 SKILL.md 已安装到本地。",
            "- 标注「全文已注入」的 skill：直接按下方全文指引执行，无需再读文件。",
            "- 其余 skill：若任务匹配其 description，用 file_read 读取 Path 获取完整指引后再执行。",
            "",
        ]
        for s in skills:
            key = f"{s.source}/{s.name}"
            desc = (s.description or "").strip() or "(无描述)"
            if len(desc) > 280:
                desc = desc[:280] + "…"
            flag = " · 全文已注入" if key in full_ids else ""
            lines.append(f"## {s.display_name} (`{key}`){flag}")
            lines.append(desc)
            lines.append(f"Path: `{s.path}`")
            lines.append("")
        return "\n".join(lines).rstrip()

    def build_full_block(self, skill: PromptSkill, *, max_chars: int = 6000) -> str:
        body = (skill.body or skill.full_content or "").strip()
        truncated = False
        if max_chars > 0 and len(body) > max_chars:
            body = body[:max_chars].rstrip() + "\n\n…(truncated)"
            truncated = True
        head = [
            f"# Skill Full Guide: {skill.display_name}",
            f"Source: `{skill.source}/{skill.name}`",
            f"Path: `{skill.path}`",
        ]
        if truncated:
            head.append(f"(body truncated to {max_chars} chars; full file at Path)")
        head.append("")
        return "\n".join(head) + body

    def build_injection_block(
        self,
        user_input: str = "",
        *,
        mode: PromptSkillMode | str | None = None,
        max_full: int | None = None,
        full_max_chars: int | None = None,
        match_threshold: float | None = None,
        skills: list[PromptSkill] | None = None,
    ) -> tuple[str, InjectionPlan]:
        """构建最终注入块 + 计划元数据。"""
        from backend.core.config import settings

        mode_s = (mode or getattr(settings, "prompt_skill_mode", "auto") or "auto").lower()
        if mode_s not in ("summary", "auto", "full"):
            mode_s = "auto"
        max_full_n = int(
            max_full
            if max_full is not None
            else getattr(settings, "prompt_skill_max_full", 2) or 2
        )
        full_chars = int(
            full_max_chars
            if full_max_chars is not None
            else getattr(settings, "prompt_skill_full_max_chars", 6000) or 6000
        )
        threshold = float(
            match_threshold
            if match_threshold is not None
            else getattr(settings, "prompt_skill_match_threshold", 0.85) or 0.85
        )

        if skills is None:
            skills = self.list_installed()
        if not skills:
            return "", InjectionPlan(mode=mode_s, summary_skills=[], full_skills=[], scores={})

        selected = self.select_full_skills(
            skills,
            user_input,
            mode=mode_s,  # type: ignore[arg-type]
            max_full=max_full_n,
            threshold=threshold,
        )
        full_ids = {f"{m.skill.source}/{m.skill.name}" for m in selected}
        scores = {f"{m.skill.source}/{m.skill.name}": round(m.score, 3) for m in selected}
        # 也记录未入选的 top 分数便于日志
        if mode_s == "auto":
            all_scores = {
                f"{s.source}/{s.name}": round(self.score_relevance(s, user_input).score, 3)
                for s in skills
            }
            scores = {**all_scores, **scores}

        summary = self.build_summary_block(skills, full_ids=full_ids)
        parts = [summary] if summary else []

        for m in selected:
            parts.append(self.build_full_block(m.skill, max_chars=full_chars))
            logger.info(
                "prompt-skills full inject: %s/%s score=%.2f reasons=%s",
                m.skill.source,
                m.skill.name,
                m.score,
                m.reasons,
            )

        block = "\n\n".join(p for p in parts if p).strip()
        plan = InjectionPlan(
            mode=mode_s,
            summary_skills=[f"{s.source}/{s.name}" for s in skills],
            full_skills=sorted(full_ids),
            scores=scores,
            block_chars=len(block),
        )
        return block, plan


_loader: PromptSkillLoader | None = None


def get_prompt_skill_loader() -> PromptSkillLoader:
    global _loader
    if _loader is None:
        _loader = PromptSkillLoader()
    return _loader
