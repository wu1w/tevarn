"""
wiki_search — 从 Wiki 知识图谱检索实体/关系，供 Agent 调用
"""

from __future__ import annotations

import json
from typing import Any

from backend.skills.base import BaseSkill


class WikiSearchSkill(BaseSkill):
    name = "wiki_search"
    description = (
        "检索本地 Wiki 知识图谱中的实体与关系。"
        "当用户问及项目概念、人物、技术依赖、图谱中已有知识时使用。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "检索关键词或实体名",
            },
            "limit": {
                "type": "integer",
                "description": "最多返回实体数（默认 8）",
                "default": 8,
            },
        },
        "required": ["query"],
    }

    async def execute(self, query: str, limit: int = 8, **kwargs: Any) -> str:
        from backend.repositories.wiki_repo import (
            AsyncWikiEntityRepository,
            AsyncWikiRelationRepository,
        )

        q = (query or "").strip()
        if not q:
            return "查询为空"
        limit = max(1, min(int(limit or 8), 30))
        entity_repo = AsyncWikiEntityRepository()
        relation_repo = AsyncWikiRelationRepository()

        entities = await entity_repo.search(q) or []
        if not entities:
            # 退化为 list_all 前缀过滤
            all_ents = await entity_repo.list_all() or []
            ql = q.lower()
            entities = [
                e
                for e in all_ents
                if ql in (e.name or "").lower()
                or ql in (e.description or "").lower()
                or any(ql in str(a).lower() for a in (e.aliases or []))
            ][:limit]
        else:
            entities = entities[:limit]

        if not entities:
            return f"Wiki 中未找到与「{q}」相关的实体。"

        lines = [f"# Wiki 检索：{q}", ""]
        for e in entities:
            lines.append(f"## {e.name} ({getattr(e, 'entity_type', 'concept')})")
            if e.description:
                lines.append(e.description)
            if e.aliases:
                lines.append(f"别名: {', '.join(e.aliases)}")
            # 关系
            try:
                rels = await relation_repo.list_by_source(e.id) or []
                rels2 = await relation_repo.list_by_target(e.id) or []
            except Exception:
                rels, rels2 = [], []
            if rels or rels2:
                lines.append("关系:")
                for r in (rels + rels2)[:12]:
                    lines.append(
                        f"- {r.relation_type}: {r.source_id} → {r.target_id}"
                        + (f" ({r.evidence})" if getattr(r, "evidence", None) else "")
                    )
            lines.append("")

        return "\n".join(lines)
