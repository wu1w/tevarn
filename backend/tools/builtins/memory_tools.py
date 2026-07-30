"""memory_graph 工具（Phase 1 Memory Graph MVP）

agent 显式读写长期记忆图：
- remember：记一条知识/决策/偏好/经验（可挂在已有节点下）
- recall：按关键词+类型召回（命中计数自增，越用越准）
- link：建立节点关系（related_to/part_of/supports/contradicts/derived_from）
- forget：删除节点（级联删边）
- subgraph：查看节点及其一度关系
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from backend.tools.base import BaseTool, ToolRiskLevel, ToolSource

logger = logging.getLogger(__name__)


class MemoryGraphTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(
            name="memory_graph",
            description=(
                "长期记忆图：跨会话记住项目知识/决策/偏好/经验。"
                "action=remember(kind,title,content,tags?,link_to?) | "
                "recall(query?,kind?,limit?) | link(from_id,to_id,relation?,note?) | "
                "forget(node_id) | subgraph(node_id)。"
                "kind: knowledge|decision|preference|experience。"
                "重要决策/用户偏好/踩坑经验应主动 remember；执行任务前先 recall 相关记忆。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["remember", "recall", "link", "forget", "subgraph"],
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["knowledge", "decision", "preference", "experience"],
                    },
                    "title": {"type": "string", "description": "remember 必填，≤200 字"},
                    "content": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "link_to": {"type": "string", "description": "remember 时挂到已有节点 id"},
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                    "from_id": {"type": "string"},
                    "to_id": {"type": "string"},
                    "relation": {
                        "type": "string",
                        "enum": ["related_to", "part_of", "supports", "contradicts", "derived_from"],
                        "default": "related_to",
                    },
                    "note": {"type": "string"},
                    "node_id": {"type": "string"},
                },
                "required": ["action"],
            },
            source=ToolSource.BUILTIN,
            risk_level=ToolRiskLevel.LOW,
        )

    async def execute(self, **kwargs: Any) -> str:
        from backend.repositories.memory_graph_repo import (
            VALID_KINDS,
            VALID_RELATIONS,
            AsyncMemoryGraphRepository,
        )

        repo = AsyncMemoryGraphRepository()
        action = str(kwargs.get("action") or "").strip()
        uid = kwargs.get("user_id") or kwargs.get("_user_id")
        user_id = None
        if uid:
            try:
                user_id = uuid.UUID(str(uid))
            except (ValueError, AttributeError):
                user_id = None

        if action == "remember":
            kind = str(kwargs.get("kind") or "").strip()
            title = str(kwargs.get("title") or "").strip()
            if kind not in VALID_KINDS:
                return f"[Error] kind 必须是 {VALID_KINDS}"
            if not title:
                return "[Error] title 必填"
            tags = kwargs.get("tags") or []
            if not isinstance(tags, list):
                tags = [str(tags)]
            node = await repo.add_node({
                "user_id": user_id,
                "kind": kind,
                "title": title[:200],
                "content": str(kwargs.get("content") or ""),
                "tags": [str(t) for t in tags][:20],
                "source": "agent",
                "source_session_id": str(kwargs.get("_session_id") or "") or None,
            })
            out = f"[remembered] {kind} #{node.id} {title}"
            link_to = str(kwargs.get("link_to") or "").strip()
            if link_to:
                try:
                    await repo.add_edge({
                        "from_id": node.id,
                        "to_id": uuid.UUID(link_to),
                        "relation": "related_to",
                    })
                    out += f"（已关联到 {link_to}）"
                except (ValueError, Exception) as e:
                    out += f"（关联失败: {e}）"
            # 二期：自动写边（受 settings.memory_graph_auto_link 控制）
            try:
                from backend.core.config import settings

                if settings.memory_graph_auto_link:
                    auto_edges = await repo.auto_link(node)
                    if auto_edges:
                        out += f"（自动关联 {len(auto_edges)} 条相似记忆）"
            except Exception as e:
                logger.debug("auto_link skipped: %s", e)
            return out

        if action == "recall":
            kind = str(kwargs.get("kind") or "").strip() or None
            if kind and kind not in VALID_KINDS:
                return f"[Error] kind 必须是 {VALID_KINDS}"
            limit = max(1, min(int(kwargs.get("limit") or 10), 50))
            nodes = await repo.recall(
                query=str(kwargs.get("query") or ""),
                kind=kind,
                limit=limit,
            )
            if not nodes:
                return "[recall] 无匹配记忆"
            lines = [f"[recall] {len(nodes)} 条记忆："]
            for n in nodes:
                preview = (n.content or "").strip().replace("\n", " ")[:120]
                lines.append(
                    f"- #{n.id} [{n.kind}] {n.title}"
                    + (f" — {preview}" if preview else "")
                    + (f"（hits={n.hit_count}）" if n.hit_count else "")
                )
            return "\n".join(lines)

        if action == "link":
            try:
                from_id = uuid.UUID(str(kwargs.get("from_id") or ""))
                to_id = uuid.UUID(str(kwargs.get("to_id") or ""))
            except (ValueError, AttributeError):
                return "[Error] link 需要合法 from_id / to_id"
            relation = str(kwargs.get("relation") or "related_to")
            if relation not in VALID_RELATIONS:
                return f"[Error] relation 必须是 {VALID_RELATIONS}"
            # 两端节点必须存在
            if await repo.get_node(from_id) is None:
                return f"[Error] from 节点不存在: {from_id}"
            if await repo.get_node(to_id) is None:
                return f"[Error] to 节点不存在: {to_id}"
            edge = await repo.add_edge({
                "from_id": from_id,
                "to_id": to_id,
                "relation": relation,
                "note": str(kwargs.get("note") or "")[:300],
            })
            return f"[linked] {from_id} --{relation}--> {to_id}（edge #{edge.id}）"

        if action == "forget":
            try:
                nid = uuid.UUID(str(kwargs.get("node_id") or ""))
            except (ValueError, AttributeError):
                return "[Error] forget 需要合法 node_id"
            ok = await repo.delete_node(nid)
            return f"[forgot] {nid}" if ok else f"[Error] 节点不存在: {nid}"

        if action == "subgraph":
            try:
                nid = uuid.UUID(str(kwargs.get("node_id") or ""))
            except (ValueError, AttributeError):
                return "[Error] subgraph 需要合法 node_id"
            node = await repo.get_node(nid)
            if node is None:
                return f"[Error] 节点不存在: {nid}"
            edges = await repo.edges_of(nid)
            lines = [f"[subgraph] #{node.id} [{node.kind}] {node.title}"]
            if node.content:
                lines.append(f"内容: {node.content[:500]}")
            if not edges:
                lines.append("（无关联边）")
            for e in edges:
                other = e.to_id if e.from_id == nid else e.from_id
                direction = "-->" if e.from_id == nid else "<--"
                other_node = await repo.get_node(other)
                other_title = other_node.title if other_node else str(other)
                lines.append(f"- {direction} [{e.relation}] {other_title} (#{other})")
            return "\n".join(lines)

        return f"[Error] 未知 action: {action}（remember|recall|link|forget|subgraph）"
