"""
Wiki 自动抽取器：从文本/文档中抽取实体与关系
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.services.wiki.schema import (
    WikiExtraction,
    WikiExtractedEntity,
    WikiExtractedRelation,
    WikiSchema,
)

logger = logging.getLogger(__name__)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[:-3].strip()
    return json.loads(text)


def _build_extraction_prompt(text: str) -> str:
    entity_types = "\n".join(
        f"- {k}: {v}" for k, v in WikiSchema.ENTITY_TYPE_LABELS.items()
    )
    relation_types = "\n".join(
        f"- {k}: {v}" for k, v in WikiSchema.RELATION_TYPE_LABELS.items()
    )
    return (
        "你是一名知识图谱抽取专家。请从下面的文本中抽取关键实体和它们之间的关系。\n\n"
        "实体类型（必须严格使用以下 10 类之一）：\n"
        f"{entity_types}\n\n"
        "关系类型（必须严格使用以下之一）：\n"
        f"{relation_types}\n\n"
        "要求：\n"
        "1. 实体名称简洁，去重；同一实体只出现一次。\n"
        "2. 为实体提供 1-2 句描述。\n"
        "3. 每个关系必须 source_name 和 target_name 都在 entities 列表中。\n"
        "4. 只返回 JSON，不要 markdown、不要解释。\n\n"
        "JSON 格式：\n"
        '{"entities":[{"name":"...","entity_type":"...","description":"...","aliases":["..."]}],'
        '"relations":[{"source_name":"...","target_name":"...","relation_type":"...","evidence":"..."}]}\n\n'
        "文本：\n"
        "---\n"
        f"{text[:12000]}\n"
        "---"
    )


async def extract_from_text(text: str) -> WikiExtraction:
    """从文本抽取 Wiki 实体和关系。"""
    if not text or len(text.strip()) < 60:
        return WikiExtraction()

    from backend.services.llm import LLMServiceFactory

    llm = LLMServiceFactory.get_service()
    prompt = _build_extraction_prompt(text)
    try:
        response = await llm.chat_complete([
            {"role": "system", "content": "你是知识图谱抽取助手，只输出合法 JSON。"},
            {"role": "user", "content": prompt},
        ])
        raw = _extract_json(response.content)
        return WikiExtraction(
            entities=[WikiExtractedEntity(**e) for e in raw.get("entities", [])],
            relations=[WikiExtractedRelation(**r) for r in raw.get("relations", [])],
        )
    except Exception as e:
        logger.warning("Wiki extraction failed: %s", e)
        return WikiExtraction()


async def extract_and_merge(
    text: str,
    existing_entities: list[Any] | None = None,
) -> WikiExtraction:
    """抽取并去重：如果文本里提到已有实体，优先使用已有名称。"""
    extraction = await extract_from_text(text)
    existing = existing_entities or []
    existing_by_name = {e.name.lower(): e for e in existing}
    for e in existing:
        for a in (e.aliases or []):
            existing_by_name[a.lower()] = e

    name_map: dict[str, str] = {}
    for ent in extraction.entities:
        key = ent.name.lower()
        if key in existing_by_name:
            name_map[ent.name] = existing_by_name[key].name

    for rel in extraction.relations:
        if rel.source_name in name_map:
            rel.source_name = name_map[rel.source_name]
        if rel.target_name in name_map:
            rel.target_name = name_map[rel.target_name]

    return extraction
