"""MemoryNode / MemoryEdge 仓库（Phase 1 Memory Graph MVP）

文本召回用 LIKE 匹配（本地 MVP 口径；向量检索后续叠加到 recall 排序）。
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import desc, or_, select

from backend.models.memory_graph import MemoryEdge, MemoryNode
from backend.repositories.base import AsyncBaseRepository

VALID_KINDS = ("knowledge", "decision", "preference", "experience")
VALID_RELATIONS = ("related_to", "part_of", "supports", "contradicts", "derived_from")


class AsyncMemoryGraphRepository(AsyncBaseRepository):
    """记忆图存取"""

    # ─────────── Node ───────────

    async def add_node(self, data: dict[str, Any]) -> MemoryNode:
        session = await self._get_session()
        try:
            obj = MemoryNode(**data)
            session.add(obj)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return obj
        except Exception:
            await session.rollback()
            raise
        finally:
            await self._close_session(session)

    async def get_node(
        self,
        node_id: uuid.UUID,
        *,
        user_id: uuid.UUID | None = None,
    ) -> MemoryNode | None:
        session = await self._get_session()
        try:
            stmt = select(MemoryNode).where(MemoryNode.id == node_id)
            if user_id is not None:
                # 归属隔离：仅本人节点，或历史无 user_id 的共享/遗留节点
                stmt = stmt.where(
                    or_(
                        MemoryNode.user_id == user_id,
                        MemoryNode.user_id.is_(None),
                    )
                )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()
        finally:
            await self._close_session(session)

    async def update_node(self, node_id: uuid.UUID, data: dict[str, Any]) -> MemoryNode | None:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(MemoryNode).where(MemoryNode.id == node_id)
            )
            obj = result.scalar_one_or_none()
            if obj is None:
                return None
            for k, v in data.items():
                setattr(obj, k, v)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return obj
        except Exception:
            await session.rollback()
            raise
        finally:
            await self._close_session(session)

    async def delete_node(self, node_id: uuid.UUID) -> bool:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(MemoryNode).where(MemoryNode.id == node_id)
            )
            obj = result.scalar_one_or_none()
            if obj is None:
                return False
            # 显式删边（不依赖 DB 层 FK CASCADE，sqlite 默认不开外键）
            from sqlalchemy import delete as sa_delete

            await session.execute(
                sa_delete(MemoryEdge).where(
                    or_(MemoryEdge.from_id == node_id, MemoryEdge.to_id == node_id)
                )
            )
            await session.delete(obj)
            await self._maybe_commit(session)
            return True
        except Exception:
            await session.rollback()
            raise
        finally:
            await self._close_session(session)

    async def recall(
        self,
        *,
        query: str = "",
        kind: str | None = None,
        limit: int = 10,
        bump_hits: bool = True,
        match_any: bool = False,
        user_id: uuid.UUID | None = None,
    ) -> list[MemoryNode]:
        """文本召回：title/content LIKE + kind 过滤；按 hit_count+置信度+新鲜度排序

        match_any=True 时 query 按空白拆词做 OR 匹配（自动注入场景：
        整句 user_input 全串 LIKE 几乎必然零命中）。
        user_id 给定时只召回本人 + 无归属遗留节点（防跨用户 IDOR）。
        """
        session = await self._get_session()
        try:
            stmt = select(MemoryNode)
            if user_id is not None:
                stmt = stmt.where(
                    or_(
                        MemoryNode.user_id == user_id,
                        MemoryNode.user_id.is_(None),
                    )
                )
            if kind:
                stmt = stmt.where(MemoryNode.kind == kind)
            q = (query or "").strip()
            if q and match_any:
                terms = [t for t in q.split() if len(t) >= 2][:8]
                if terms:
                    stmt = stmt.where(or_(*[
                        or_(
                            MemoryNode.title.like(f"%{t}%"),
                            MemoryNode.content.like(f"%{t}%"),
                        )
                        for t in terms
                    ]))
            elif q:
                like = f"%{q}%"
                stmt = stmt.where(
                    or_(
                        MemoryNode.title.like(like),
                        MemoryNode.content.like(like),
                    )
                )
            stmt = stmt.order_by(
                desc(MemoryNode.hit_count),
                desc(MemoryNode.confidence),
                desc(MemoryNode.updated_at),
            ).limit(limit)
            result = await session.execute(stmt)
            nodes = list(result.scalars().all())
            if bump_hits and nodes:
                for n in nodes:
                    n.hit_count = (n.hit_count or 0) + 1
                await self._maybe_commit(session)
            return nodes
        except Exception:
            await session.rollback()
            raise
        finally:
            await self._close_session(session)

    async def count_nodes(self) -> int:
        from sqlalchemy import func

        session = await self._get_session()
        try:
            result = await session.execute(select(func.count(MemoryNode.id)))
            return int(result.scalar() or 0)
        finally:
            await self._close_session(session)

    # ─────────── Edge ───────────

    async def add_edge(self, data: dict[str, Any]) -> MemoryEdge:
        session = await self._get_session()
        try:
            obj = MemoryEdge(**data)
            session.add(obj)
            await self._maybe_commit(session)
            await session.refresh(obj)
            return obj
        except Exception:
            await session.rollback()
            raise
        finally:
            await self._close_session(session)

    async def edges_of(self, node_id: uuid.UUID) -> list[MemoryEdge]:
        session = await self._get_session()
        try:
            result = await session.execute(
                select(MemoryEdge).where(
                    or_(MemoryEdge.from_id == node_id, MemoryEdge.to_id == node_id)
                )
            )
            return list(result.scalars().all())
        finally:
            await self._close_session(session)

    async def auto_link(
        self,
        node: MemoryNode,
        *,
        max_edges: int = 3,
        min_score: int = 2,
        candidate_limit: int = 300,
    ) -> list[MemoryEdge]:
        """自动写边（二期）：与相似节点建 related_to 边。

        打分（确定性，零 LLM 依赖）：
        - 共同 tag ×2
        - title/content 词集合交集 ×1（中英混合按非字母数字切分）

        分 ≥ min_score 的取 top-max_edges 建边；已有边（任意方向）跳过。
        """
        import re

        def _tokens(text: str) -> set[str]:
            return {t for t in re.split(r"[^0-9A-Za-z一-鿿]+", (text or "").lower()) if len(t) >= 2}

        my_tags = {str(t).lower() for t in (node.tags or [])}
        my_words = _tokens(f"{node.title} {node.content}")

        session = await self._get_session()
        try:
            stmt = select(MemoryNode).where(MemoryNode.id != node.id)
            if node.user_id is not None:
                stmt = stmt.where(MemoryNode.user_id == node.user_id)
            stmt = stmt.order_by(desc(MemoryNode.updated_at)).limit(candidate_limit)
            candidates = list((await session.execute(stmt)).scalars().all())

            existing = await self.edges_of(node.id)
            linked = {e.to_id for e in existing if e.from_id == node.id} | {
                e.from_id for e in existing if e.to_id == node.id
            }

            scored: list[tuple[int, MemoryNode]] = []
            for cand in candidates:
                if cand.id in linked:
                    continue
                score = 2 * len(my_tags & {str(t).lower() for t in (cand.tags or [])})
                score += len(my_words & _tokens(f"{cand.title} {cand.content}"))
                # 中文无空格分词弱，title 互含兜底（如 "Takton 部署" vs "Takton 部署规范"）
                if cand.title and node.title and (cand.title in node.title or node.title in cand.title):
                    score += 2
                if score >= min_score:
                    scored.append((score, cand))
            scored.sort(key=lambda x: x[0], reverse=True)

            created: list[MemoryEdge] = []
            for _score, cand in scored[:max_edges]:
                edge = await self.add_edge({
                    "from_id": node.id,
                    "to_id": cand.id,
                    "relation": "related_to",
                    "note": "auto-link",
                })
                created.append(edge)
            return created
        finally:
            await self._close_session(session)
