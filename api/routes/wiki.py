"""
Wiki Graph 路由
知识图谱 API

注意：WikiEntity/WikiRelation 是本系统（个人专属 Agent 终端）中的全局共享资源，
数据模型未包含 user_id 字段，因此不做按用户隔离。
"""

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError

from backend.core.unit_of_work import UnitOfWork
from backend.repositories import CtxItemRepository, SessionRepository, WikiEntityRepository, WikiRelationRepository
from backend.schemas.user import UserRead
from backend.schemas.wiki import (
    WikiEntityCreate,
    WikiEntityRead,
    WikiEntityUpdate,
    WikiImportRequest,
    WikiImportResult,
    WikiImportSource,
    WikiRelationCreate,
    WikiRelationRead,
)
from backend.services.llm import LLMService, LLMServiceFactory

from ..dependencies import (
    get_current_user,
    get_ctx_item_repo,
    get_session_repo,
    get_wiki_entity_repo,
    get_wiki_relation_repo,
)

router = APIRouter(prefix="/wiki", tags=["Wiki Graph"])


# ── Entity ──

@router.get("/entities", response_model=list[WikiEntityRead])
async def list_entities(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[WikiEntityRepository, Depends(get_wiki_entity_repo)],
    q: str = "",
):
    """列出或搜索实体"""
    if q:
        return await repo.search(q) or []
    return await repo.list_all() or []


@router.post("/entities", response_model=WikiEntityRead)
async def create_entity(
    data: WikiEntityCreate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[WikiEntityRepository, Depends(get_wiki_entity_repo)],
):
    """创建实体"""
    existing = await repo.get_by_name(data.name)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Entity name already exists")
    try:
        return await repo.create(data.model_dump())
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Entity name already exists") from exc


@router.get("/entities/{entity_id}", response_model=WikiEntityRead)
async def get_entity(
    entity_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[WikiEntityRepository, Depends(get_wiki_entity_repo)],
):
    """获取实体详情"""
    entity = await repo.get_by_id(entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    if getattr(entity, "user_id", None) and entity.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return entity


@router.put("/entities/{entity_id}", response_model=WikiEntityRead)
async def update_entity(
    entity_id: uuid.UUID,
    data: WikiEntityUpdate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[WikiEntityRepository, Depends(get_wiki_entity_repo)],
):
    """更新实体"""
    entity = await repo.get_by_id(entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    if getattr(entity, "user_id", None) and entity.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    if data.name is not None:
        existing = await repo.get_by_name(data.name)
        if existing is not None and existing.id != entity_id:
            raise HTTPException(status_code=409, detail="Entity name already exists")
    try:
        entity = await repo.update(entity_id, data.model_dump(exclude_unset=True))
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Entity name already exists") from exc
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity


@router.delete("/entities/{entity_id}")
async def delete_entity(
    entity_id: uuid.UUID,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    repo: Annotated[WikiEntityRepository, Depends(get_wiki_entity_repo)],
):
    """删除实体"""
    entity = await repo.get_by_id(entity_id)
    if entity is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    if getattr(entity, "user_id", None) and entity.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    success = await repo.delete(entity_id)
    if not success:
        raise HTTPException(status_code=404, detail="Entity not found")
    return {"deleted": True}


# ── Relation ──

@router.get("/relations", response_model=list[WikiRelationRead])
async def list_relations(
    current_user: Annotated[UserRead, Depends(get_current_user)],
    relation_repo: Annotated[WikiRelationRepository, Depends(get_wiki_relation_repo)],
    source_id: uuid.UUID | None = None,
    target_id: uuid.UUID | None = None,
):
    """列出关系"""
    if source_id:
        return await relation_repo.list_by_source(source_id) or []
    if target_id:
        return await relation_repo.list_by_target(target_id) or []
    return []


@router.post("/relations", response_model=WikiRelationRead)
async def create_relation(
    data: WikiRelationCreate,
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    """创建关系（source/target 存在性检查与 relation 创建在同一事务）"""
    if data.source_id == data.target_id:
        raise HTTPException(status_code=400, detail="source_id and target_id cannot be the same entity")

    async with UnitOfWork() as uow:
        source = await uow.wiki_entities.get_by_id(data.source_id)
        if source is None:
            raise HTTPException(status_code=404, detail=f"Source entity {data.source_id} not found")
        target = await uow.wiki_entities.get_by_id(data.target_id)
        if target is None:
            raise HTTPException(status_code=404, detail=f"Target entity {data.target_id} not found")

        return await uow.wiki_relations.create(data.model_dump())


# ── Import / LLM-powered extraction ──

_VALID_ENTITY_TYPES = {"concept", "person", "project", "tech"}
_VALID_RELATION_TYPES = {"uses", "depends_on", "related_to", "part_of"}


def _normalize_name(name: str) -> str:
    return (name or "").strip()


def _normalize_entity_type(value: str | None) -> str:
    t = (value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return t if t in _VALID_ENTITY_TYPES else "concept"


def _normalize_relation_type(value: str | None) -> str:
    t = (value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return t if t in _VALID_RELATION_TYPES else "related_to"


def _extract_llm_json(content: str) -> dict[str, Any]:
    """从 LLM 响应中提取 JSON，支持 markdown code fence。"""
    text = content.strip()
    if text.startswith("```"):
        # 去掉开头的 ```json 等
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.endswith("```"):
            text = text[:-3].strip()
    return json.loads(text)


def _build_extraction_prompt(raw_material: str, source: WikiImportSource) -> str:
    source_hint = {
        WikiImportSource.text: "unstructured text/markdown",
        WikiImportSource.json: "a JSON dump that may contain entities/relations",
        WikiImportSource.context: "context/memory items from the agent",
    }.get(source, "raw data")

    return (
        "You are a knowledge-graph cleaning and classification assistant.\n"
        "Given the following {source_hint}, extract or clean entities and relations.\n"
        "Rules:\n"
        "1. Normalize entity names (trim, capitalize proper nouns sensibly).\n"
        "2. Deduplicate entities by name; merge aliases.\n"
        "3. entity_type MUST be one of: concept, person, project, tech.\n"
        "4. relation_type MUST be one of: uses, depends_on, related_to, part_of.\n"
        "5. Only include relations whose source/target entities also appear in entities list.\n"
        "6. Return ONLY valid JSON in the following shape (no markdown, no explanation):\n\n"
        "{{\n"
        '  "entities": [\n'
        '    {{"name": "...", "entity_type": "...", "description": "...", "aliases": ["..."]}}\n'
        "  ],\n"
        '  "relations": [\n'
        '    {{"source_name": "...", "target_name": "...", "relation_type": "...", "evidence": "..."}}\n'
        "  ]\n"
        "}}\n\n"
        "Raw data:\n"
        "---\n"
        "{raw_material}\n"
        "---"
    ).format(source_hint=source_hint, raw_material=raw_material[:12000])


async def _ensure_session_owned(
    session_id: uuid.UUID | None,
    current_user: UserRead,
    session_repo: SessionRepository,
) -> None:
    """校验 session_id 归属当前用户（空表示全局资源）"""
    if session_id is None:
        return
    session = await session_repo.get_by_id(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if getattr(session, "user_id", None) and session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")


async def _build_raw_material(
    request: WikiImportRequest,
    ctx_repo: CtxItemRepository,
) -> str:
    if request.source == WikiImportSource.text or request.source == WikiImportSource.json:
        return request.content or ""

    if request.source == WikiImportSource.context:
        items = await ctx_repo.list_by_session(
            session_id=request.session_id,
            limit=int(request.options.get("limit", 500)),
        )
        parts = []
        for item in items:
            line = f"[{item.scope}/{item.kind}] {item.key}: {item.value}"
            if item.origin:
                line += f" (origin: {item.origin})"
            parts.append(line)
        return "\n".join(parts)

    return ""


@router.post("/import", response_model=WikiImportResult)
async def import_wiki(
    request: WikiImportRequest,
    current_user: Annotated[UserRead, Depends(get_current_user)],
    ctx_repo: Annotated[CtxItemRepository, Depends(get_ctx_item_repo)],
    session_repo: Annotated[SessionRepository, Depends(get_session_repo)],
):
    """从文本/JSON/Context 导入数据，调用 LLM 清洗、分类后写入 Wiki Graph。"""
    await _ensure_session_owned(request.session_id, current_user, session_repo)
    raw_material = await _build_raw_material(request, ctx_repo)
    if not raw_material.strip():
        raise HTTPException(status_code=400, detail="No importable content provided")

    dry_run = request.options.get("dry_run", False)
    if dry_run:
        try:
            extracted = json.loads(raw_material)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"Dry-run content is not valid JSON: {e}")
    else:
        llm = LLMServiceFactory.get_service()
        prompt = _build_extraction_prompt(raw_material, request.source)
        try:
            response = await llm.chat_complete([
                {"role": "system", "content": "You are a knowledge-graph cleaning assistant."},
                {"role": "user", "content": prompt},
            ])
            extracted = _extract_llm_json(response.content)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=502, detail=f"LLM returned invalid JSON: {e}")
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"LLM extraction failed: {e}")

    raw_entities = extracted.get("entities", [])
    raw_relations = extracted.get("relations", [])

    if not raw_entities:
        return WikiImportResult(detail=["No entities extracted"])

    update_existing = request.options.get("update_existing", True)

    async with UnitOfWork() as uow:
        existing_entities = await uow.wiki_entities.list_all(limit=2000)
        # case-insensitive name -> entity
        existing_by_name: dict[str, Any] = {}
        for e in existing_entities:
            existing_by_name[_normalize_name(e.name).lower()] = e

        name_to_entity: dict[str, Any] = {}
        result = WikiImportResult()

        for item in raw_entities:
            name = _normalize_name(item.get("name", ""))
            if not name:
                result.skipped += 1
                continue

            entity_type = _normalize_entity_type(item.get("entity_type"))
            description = (item.get("description") or "").strip()
            aliases = [a.strip() for a in item.get("aliases", []) if a and a.strip()]

            key = name.lower()
            existing = existing_by_name.get(key)

            if existing:
                if update_existing:
                    merged_aliases = list(set((existing.aliases or []) + aliases))
                    update_data: dict[str, Any] = {
                        "entity_type": entity_type,
                        "aliases": merged_aliases,
                    }
                    if description:
                        update_data["description"] = description
                    await uow.wiki_entities.update(existing.id, update_data)
                    result.entities_updated += 1
                name_to_entity[name] = existing_by_name[key]
            else:
                entity = await uow.wiki_entities.create({
                    "name": name,
                    "entity_type": entity_type,
                    "description": description,
                    "aliases": aliases,
                    "meta": {"imported_from": request.source.value},
                })
                existing_by_name[name.lower()] = entity
                name_to_entity[name] = entity
                result.entities_created += 1

        # Build case-insensitive name -> entity for relation lookup
        lookup = {k.lower(): v for k, v in name_to_entity.items()}
        lookup.update(existing_by_name)

        seen_relations: set[tuple[str, str, str]] = set()
        for item in raw_relations:
            source_name = _normalize_name(item.get("source_name", ""))
            target_name = _normalize_name(item.get("target_name", ""))
            relation_type = _normalize_relation_type(item.get("relation_type"))
            evidence = (item.get("evidence") or "").strip()

            source = lookup.get(source_name.lower())
            target = lookup.get(target_name.lower())
            if not source or not target or source.id == target.id:
                result.skipped += 1
                continue

            rel_key = (str(source.id), str(target.id), relation_type)
            if rel_key in seen_relations:
                result.skipped += 1
                continue
            seen_relations.add(rel_key)

            existing_rel = await uow.wiki_relations.get_between(source.id, target.id)
            if existing_rel:
                result.skipped += 1
                continue

            await uow.wiki_relations.create({
                "source_id": source.id,
                "target_id": target.id,
                "relation_type": relation_type,
                "evidence": evidence,
            })
            result.relations_created += 1

    result.detail.append(
        f"Extracted {len(raw_entities)} entities and {len(raw_relations)} relations from LLM"
    )
    return result
