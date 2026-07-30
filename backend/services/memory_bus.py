"""Phase 3.1 记忆总线：唯一业务写入口 + 跨源 recall。

权威路由见 docs/design/MEMORY_BUS.md。
底层仍复用 IdentityRegistry / MemoryGraph / Entity；业务层（工具、Writer）应走本模块。
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

logger = logging.getLogger(__name__)

# identity 编制真源 kinds
IDENTITY_KINDS = frozenset({"persona", "duty", "methodology", "preference", "experience"})
# memory graph kinds
GRAPH_KINDS = frozenset({"knowledge", "decision", "preference", "experience"})
ENTITY_KINDS = frozenset({"entity", "fact"})
WIKI_KINDS = frozenset({"wiki"})

MemorySource = Literal["identity", "graph", "entity", "wiki"]


@dataclass
class MemoryWriteResult:
    ok: bool
    source: MemorySource
    id: str = ""
    kind: str = ""
    version: int = 1
    message: str = ""
    raw: Any = None


@dataclass
class MemoryHit:
    source: MemorySource
    id: str
    kind: str
    content: str
    title: str = ""
    score: float = 0.0
    freshness: str = ""  # iso or relative hint
    version: int = 1
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "content": self.content,
            "score": self.score,
            "freshness": self.freshness,
            "version": self.version,
            "meta": self.meta,
        }


def _route_kind(kind: str, *, identity_id: Any | None) -> MemorySource:
    k = (kind or "").strip().lower()
    if k in ENTITY_KINDS:
        return "entity"
    if k in WIKI_KINDS:
        return "wiki"
    if k in IDENTITY_KINDS and identity_id:
        return "identity"
    if k in GRAPH_KINDS:
        return "graph"
    if k in IDENTITY_KINDS:
        # 无 identity → 落到 graph，避免丢写
        return "graph"
    # 默认 graph
    return "graph"


def _score_text(query: str, text: str) -> float:
    if not query or not text:
        return 0.0
    tokens = [t for t in re.split(r"[\s,，。；;、/\\]+", query.lower()) if len(t) >= 2]
    if not tokens:
        return 0.0
    low = text.lower()
    hits = sum(1 for t in tokens if t in low)
    return float(hits) / max(1, len(tokens))


def _freshness_iso(obj: Any) -> str:
    for attr in ("updated_at", "created_at", "last_mentioned_at"):
        v = getattr(obj, attr, None)
        if v is None:
            continue
        if hasattr(v, "isoformat"):
            return v.isoformat()
        return str(v)
    return ""


async def remember(
    kind: str,
    content: str,
    *,
    source_run_id: str | uuid.UUID | None = None,
    confidence: float = 1.0,
    identity_id: Any | None = None,
    user_id: Any | None = None,
    title: str | None = None,
    tags: list[str] | None = None,
    meta: dict[str, Any] | None = None,
    approved_by: str | None = None,
    source: str = "agent",
) -> MemoryWriteResult:
    """统一写入。"""
    k = (kind or "").strip().lower()
    body = (content or "").strip()
    if not body and not (title or "").strip():
        return MemoryWriteResult(ok=False, source="graph", message="content/title 为空")

    route = _route_kind(k, identity_id=identity_id)
    meta = dict(meta or {})
    if source_run_id:
        meta["source_run_id"] = str(source_run_id)
    meta["confidence"] = float(confidence)

    try:
        if route == "identity":
            return await _write_identity(
                k,
                body,
                identity_id=identity_id,
                approved_by=approved_by or "memory_bus",
                source=source if source in ("manual", "distilled", "system", "hire", "agent") else "manual",
            )
        if route == "entity":
            return await _write_entity(
                body,
                user_id=user_id,
                title=title,
                meta=meta,
            )
        if route == "wiki":
            return MemoryWriteResult(
                ok=False,
                source="wiki",
                message="wiki 写入请走 wiki API / 人工导入；总线本期仅约定不直写 Qdrant",
            )
        # graph
        gkind = k if k in GRAPH_KINDS else "knowledge"
        return await _write_graph(
            gkind,
            body,
            title=title or body[:80],
            tags=tags,
            user_id=user_id,
            confidence=confidence,
            meta=meta,
            source=source,
        )
    except Exception as e:
        logger.warning("memory_bus.remember failed: %s", e)
        return MemoryWriteResult(ok=False, source=route, message=str(e)[:300])


async def supersede(
    ref: str | dict[str, Any],
    new_content: str,
    *,
    approved_by: str,
    source_run_id: str | uuid.UUID | None = None,
) -> MemoryWriteResult:
    """版本取代。ref 可为 'identity:<uuid>' / 'graph:<uuid>' / 'entity:<uuid>' 或 dict。"""
    source, eid = _parse_ref(ref)
    if not eid:
        return MemoryWriteResult(ok=False, source="identity", message="invalid ref")
    if not approved_by:
        return MemoryWriteResult(ok=False, source=source, message="approved_by required")

    try:
        if source == "identity":
            from backend.kernel import get_kernel

            reg = get_kernel().identity_registry
            entry = await reg.supersede_memory(eid, new_content, approved_by=approved_by)
            return MemoryWriteResult(
                ok=True,
                source="identity",
                id=str(entry.id),
                kind=str(getattr(entry, "kind", "")),
                version=int(getattr(entry, "version", 1) or 1),
                raw=entry,
            )
        if source == "graph":
            return await _supersede_graph(eid, new_content, approved_by=approved_by)
        if source == "entity":
            return await _supersede_entity(eid, new_content, approved_by=approved_by)
    except Exception as e:
        logger.warning("memory_bus.supersede failed: %s", e)
        return MemoryWriteResult(ok=False, source=source, message=str(e)[:300])
    return MemoryWriteResult(ok=False, source=source, message="unsupported source")


async def recall(
    query: str,
    *,
    kinds: list[str] | None = None,
    top_k: int = 8,
    identity_id: Any | None = None,
    user_id: Any | None = None,
) -> list[MemoryHit]:
    """跨源检索，标注来源与新鲜度。"""
    top_k = max(1, min(int(top_k or 8), 40))
    kinds_set = {k.strip().lower() for k in (kinds or []) if str(k).strip()}
    hits: list[MemoryHit] = []

    want_identity = not kinds_set or bool(kinds_set & IDENTITY_KINDS) or "identity" in kinds_set
    want_graph = not kinds_set or bool(kinds_set & GRAPH_KINDS) or "graph" in kinds_set
    want_entity = not kinds_set or bool(kinds_set & ENTITY_KINDS) or "entity" in kinds_set

    if want_identity and identity_id:
        hits.extend(await _recall_identity(query, identity_id=identity_id, kinds=kinds_set))
    if want_graph:
        hits.extend(await _recall_graph(query, kinds=kinds_set, user_id=user_id))
    if want_entity:
        hits.extend(await _recall_entity(query, user_id=user_id))

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:top_k]


# ── internal writers ──────────────────────────────────────────


async def _write_identity(
    kind: str,
    content: str,
    *,
    identity_id: Any,
    approved_by: str,
    source: str,
) -> MemoryWriteResult:
    from backend.kernel import get_kernel

    reg = get_kernel().identity_registry
    # distilled 强制 approved_by；agent 写经验用 manual/system
    src = source
    if src == "agent":
        src = "manual"
    entry = await reg.add_memory(
        identity_id, kind, content, source=src, approved_by=approved_by
    )
    return MemoryWriteResult(
        ok=True,
        source="identity",
        id=str(entry.id),
        kind=kind,
        version=int(getattr(entry, "version", 1) or 1),
        raw=entry,
    )


async def _write_graph(
    kind: str,
    content: str,
    *,
    title: str,
    tags: list[str] | None,
    user_id: Any,
    confidence: float,
    meta: dict[str, Any],
    source: str,
) -> MemoryWriteResult:
    from backend.repositories.memory_graph_repo import AsyncMemoryGraphRepository

    uid = None
    if user_id:
        try:
            uid = uuid.UUID(str(user_id))
        except (ValueError, AttributeError):
            uid = None
    node = await AsyncMemoryGraphRepository().add_node(
        {
            "user_id": uid,
            "kind": kind,
            "title": (title or content[:80])[:200],
            "content": content,
            "tags": list(tags or [])[:20],
            "source": source if source in ("manual", "agent", "session") else "agent",
            "source_session_id": str(meta.get("session_id") or "") or None,
            "confidence": float(confidence),
        }
    )
    return MemoryWriteResult(
        ok=True,
        source="graph",
        id=str(node.id),
        kind=kind,
        version=1,
        raw=node,
    )


async def _write_entity(
    content: str,
    *,
    user_id: Any,
    title: str | None,
    meta: dict[str, Any],
) -> MemoryWriteResult:
    from backend.database import get_db_context
    from backend.models.entity import Entity

    name = (title or content[:64] or "fact").strip()[:128]
    uid = None
    if user_id:
        try:
            uid = uuid.UUID(str(user_id))
        except (ValueError, AttributeError):
            uid = None
    now = datetime.now(timezone.utc).isoformat()
    async with get_db_context() as session:
        obj = Entity(
            user_id=uid,
            name=name,
            entity_type=str(meta.get("entity_type") or "custom")[:32],
            description=content,
            attributes={
                **(meta if isinstance(meta, dict) else {}),
                "version": 1,
                "superseded_by": None,
            },
            status="active",
            first_mentioned_at=now,
            last_mentioned_at=now,
        )
        session.add(obj)
        await session.commit()
        await session.refresh(obj)
    return MemoryWriteResult(
        ok=True,
        source="entity",
        id=str(obj.id),
        kind="entity",
        version=1,
        raw=obj,
    )


async def _supersede_graph(
    node_id: str, new_content: str, *, approved_by: str
) -> MemoryWriteResult:
    from backend.repositories.memory_graph_repo import AsyncMemoryGraphRepository

    repo = AsyncMemoryGraphRepository()
    old = await repo.get_node(uuid.UUID(str(node_id)))
    if old is None:
        return MemoryWriteResult(ok=False, source="graph", message="node not found")
    # 软 supersede：旧节点 content 标记 + 新节点 + 边 derived_from
    new = await repo.add_node(
        {
            "user_id": old.user_id,
            "kind": old.kind,
            "title": old.title,
            "content": new_content,
            "tags": list(old.tags or []),
            "source": "manual",
            "confidence": float(getattr(old, "confidence", 1.0) or 1.0),
        }
    )
    try:
        await repo.add_edge(
            {
                "from_id": new.id,
                "to_id": old.id,
                "relation": "derived_from",
                "note": f"supersede by {approved_by}",
            }
        )
    except Exception:
        pass
    # 标记旧节点（title 前缀 + 低置信度，recall 过滤）
    await repo.update_node(
        old.id,
        {
            "title": f"[superseded] {old.title}"[:200],
            "confidence": 0.0,
            "tags": list(dict.fromkeys([*(old.tags or []), "superseded"])),
        },
    )
    return MemoryWriteResult(
        ok=True,
        source="graph",
        id=str(new.id),
        kind=str(old.kind),
        version=2,
        raw=new,
    )


async def _supersede_entity(
    entity_id: str, new_content: str, *, approved_by: str
) -> MemoryWriteResult:
    from sqlalchemy import select

    from backend.database import get_db_context
    from backend.models.entity import Entity

    eid = uuid.UUID(str(entity_id))
    async with get_db_context() as session:
        old = (
            await session.execute(select(Entity).where(Entity.id == eid))
        ).scalar_one_or_none()
        if old is None:
            return MemoryWriteResult(ok=False, source="entity", message="entity not found")
        attrs = dict(old.attributes or {})
        ver = int(attrs.get("version") or 1) + 1
        new = Entity(
            user_id=old.user_id,
            name=old.name,
            entity_type=old.entity_type,
            description=new_content,
            attributes={
                **attrs,
                "version": ver,
                "supersedes": str(old.id),
                "approved_by": approved_by,
            },
            status="active",
            first_mentioned_at=old.first_mentioned_at,
            last_mentioned_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(new)
        await session.flush()
        old.status = "archived"
        attrs["superseded_by"] = str(new.id)
        old.attributes = attrs
        await session.commit()
        await session.refresh(new)
    return MemoryWriteResult(
        ok=True,
        source="entity",
        id=str(new.id),
        kind="entity",
        version=ver,
        raw=new,
    )


async def _recall_identity(
    query: str, *, identity_id: Any, kinds: set[str]
) -> list[MemoryHit]:
    from backend.kernel import get_kernel
    from backend.kernel.crew_memory import TOMBSTONE_MARKERS

    reg = get_kernel().identity_registry
    try:
        entries = await reg.current_memory(identity_id)
    except Exception as e:
        logger.debug("recall identity failed: %s", e)
        return []
    out: list[MemoryHit] = []
    for m in entries or []:
        content = str(getattr(m, "content", "") or "")
        if any(content.startswith(t) or content == t for t in TOMBSTONE_MARKERS):
            continue
        kind = str(getattr(m, "kind", "") or "")
        if kinds and kind not in kinds and "identity" not in kinds:
            if kinds & IDENTITY_KINDS and kind not in kinds:
                continue
        score = _score_text(query, content) if query else 0.5
        if query and score <= 0:
            continue
        out.append(
            MemoryHit(
                source="identity",
                id=str(m.id),
                kind=kind,
                content=content,
                title=kind,
                score=score + 0.2,  # 编制真源略加权
                freshness=_freshness_iso(m),
                version=int(getattr(m, "version", 1) or 1),
            )
        )
    return out


async def _recall_graph(
    query: str, *, kinds: set[str], user_id: Any
) -> list[MemoryHit]:
    from backend.repositories.memory_graph_repo import (
        VALID_KINDS,
        AsyncMemoryGraphRepository,
    )

    kind_filter = None
    if kinds:
        inter = [k for k in kinds if k in VALID_KINDS]
        if len(inter) == 1:
            kind_filter = inter[0]
        elif not inter and kinds & (ENTITY_KINDS | {"identity", "entity", "wiki"}):
            # 只查 entity/identity 时跳过 graph
            if not (kinds & set(VALID_KINDS)):
                return []
    try:
        nodes = await AsyncMemoryGraphRepository().recall(
            query=query or "",
            kind=kind_filter,
            limit=20,
            bump_hits=False,
            match_any=True,  # 整句 LIKE 几乎零命中；按词 OR
        )
    except Exception as e:
        logger.debug("recall graph failed: %s", e)
        return []
    out: list[MemoryHit] = []
    for n in nodes or []:
        title = str(getattr(n, "title", "") or "")
        if title.startswith("[superseded]") or "superseded" in (getattr(n, "tags", None) or []):
            continue
        if float(getattr(n, "confidence", 1.0) or 0) <= 0:
            continue
        kind = str(getattr(n, "kind", "") or "")
        if kinds and kind not in kinds and "graph" not in kinds:
            if kinds & set(VALID_KINDS) and kind not in kinds:
                continue
        content = str(getattr(n, "content", "") or "")
        score = _score_text(query, f"{title} {content}") if query else 0.4
        out.append(
            MemoryHit(
                source="graph",
                id=str(n.id),
                kind=kind,
                title=title,
                content=content,
                score=score,
                freshness=_freshness_iso(n),
            )
        )
    return out


async def _recall_entity(query: str, *, user_id: Any) -> list[MemoryHit]:
    from sqlalchemy import select

    from backend.database import get_db_context
    from backend.models.entity import Entity

    try:
        async with get_db_context() as session:
            q = select(Entity).where(Entity.status == "active")
            if user_id:
                try:
                    uid = uuid.UUID(str(user_id))
                    q = q.where(Entity.user_id == uid)
                except (ValueError, AttributeError):
                    pass
            rows = list((await session.execute(q.limit(50))).scalars().all())
    except Exception as e:
        logger.debug("recall entity failed: %s", e)
        return []
    out: list[MemoryHit] = []
    for e in rows:
        attrs = e.attributes or {}
        if attrs.get("superseded_by"):
            continue
        text = f"{e.name} {e.description or ''}"
        score = _score_text(query, text) if query else 0.3
        if query and score <= 0:
            continue
        out.append(
            MemoryHit(
                source="entity",
                id=str(e.id),
                kind="entity",
                title=str(e.name),
                content=str(e.description or ""),
                score=score,
                freshness=str(e.last_mentioned_at or ""),
                version=int(attrs.get("version") or 1),
            )
        )
    return out


def _parse_ref(ref: str | dict[str, Any]) -> tuple[MemorySource, str]:
    if isinstance(ref, dict):
        src = str(ref.get("source") or "identity")
        return src, str(ref.get("id") or "")  # type: ignore[return-value]
    s = str(ref or "").strip()
    if ":" in s:
        a, b = s.split(":", 1)
        if a in ("identity", "graph", "entity", "wiki"):
            return a, b  # type: ignore[return-value]
    return "identity", s


__all__ = [
    "MemoryHit",
    "MemoryWriteResult",
    "remember",
    "recall",
    "supersede",
]
