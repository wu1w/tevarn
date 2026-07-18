"""Entity Service — 长期记忆实体提取与召回

从对话中自动提取实体（项目、人名、偏好等），支持跨会话召回。
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from backend.database import get_db_context
from backend.repositories.entity_repo import EntityRepository

logger = logging.getLogger(__name__)


class EntityService:
    """实体记忆服务"""

    # 实体类型关键词映射
    TYPE_KEYWORDS = {
        "project": ["项目", "project", "工程", "repo", "仓库", "代码库", "版本", "迭代", "发布"],
        "person": ["@","张三", "李四", "王五", "同事", "负责人", "老板", "经理", "客户"],
        "preference": ["偏好", "喜欢", "习惯", "设置", "默认", "开启", "关闭", "启用"],
        "topic": ["主题", "话题", "讨论", "议题", "方向", "领域"],
        "tool": ["工具", "软件", "平台", "系统", "服务", "API", "接口"],
        "device": ["设备", "电脑", "服务器", "主机", "机器", "节点"],
    }

    async def extract_from_text(
        self,
        text: str,
        user_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
    ) -> list[dict[str, Any]]:
        """从文本中提取实体（简单规则 + 关键词匹配）"""
        if not text or len(text) < 5:
            return []

        entities: list[dict[str, Any]] = []
        seen_names: set[str] = set()

        # 提取 @提及
        mentions = re.findall(r"@(\w+)", text)
        for name in mentions:
            if name not in seen_names and len(name) >= 2:
                seen_names.add(name)
                entities.append({
                    "name": name,
                    "entity_type": "person",
                    "description": f"在对话中被提及",
                    "confidence": 0.8,
                })

        # 提取项目/仓库名（常见模式）
        project_patterns = [
            r"(?:项目|工程|repo|仓库)[：:\s]+([A-Za-z0-9_\-一-鿿]+)",
            r"([A-Z][a-z]+[A-Z][a-zA-Z0-9]*)",  # CamelCase
            r"([a-z]+-[a-z]+-[a-z]+)",  # kebab-case
        ]
        for pattern in project_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                if match not in seen_names and len(match) >= 2:
                    seen_names.add(match)
                    entities.append({
                        "name": match,
                        "entity_type": "project",
                        "description": "从对话中提取的项目",
                        "confidence": 0.6,
                    })

        # 提取偏好设置
        pref_patterns = [
            r"(?:偏好|设置|默认)[：:\s]+(.+?)(?:[，。,\.\n]|$)",
            r"(?:开启|关闭|启用|禁用)[：:\s]*(\w+)",
        ]
        for pattern in pref_patterns:
            matches = re.findall(pattern, text)
            for match in matches:
                name = match.strip()[:50]
                if name and name not in seen_names:
                    seen_names.add(name)
                    entities.append({
                        "name": name,
                        "entity_type": "preference",
                        "description": "用户偏好设置",
                        "confidence": 0.5,
                    })

        # 去重并限制数量
        return entities[:10]

    async def save_entities(
        self,
        entities: list[dict[str, Any]],
        user_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
    ) -> int:
        """保存提取的实体到数据库"""
        if not entities or not user_id:
            return 0

        now = datetime.now(timezone.utc).isoformat()
        saved = 0

        async with get_db_context() as db:
            repo = EntityRepository(db)
            for ent in entities:
                name = ent.get("name", "").strip()
                if not name or len(name) < 2:
                    continue

                existing = await repo.get_by_name(name, user_id=user_id)
                if existing:
                    # 更新提及次数和最后提及时间
                    await repo.update(existing.id, {
                        "mention_count": existing.mention_count + 1,
                        "last_mentioned_at": now,
                    })
                else:
                    # 创建新实体
                    await repo.create({
                        "user_id": user_id,
                        "name": name,
                        "entity_type": ent.get("entity_type", "custom"),
                        "description": ent.get("description", ""),
                        "attributes": {},
                        "source_session_id": str(session_id) if session_id else None,
                        "first_mentioned_at": now,
                        "last_mentioned_at": now,
                    })
                    saved += 1

        if saved:
            logger.info(f"Saved {saved} new entities for user {user_id}")
        return saved

    async def recall(
        self,
        query: str,
        user_id: uuid.UUID | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """召回与查询相关的实体记忆"""
        if not user_id:
            return []

        async with get_db_context() as db:
            repo = EntityRepository(db)
            entities = await repo.search(query, user_id=user_id, limit=limit)
            return [
                {
                    "name": e.name,
                    "entity_type": e.entity_type,
                    "description": e.description,
                    "attributes": e.attributes or {},
                    "mention_count": e.mention_count,
                }
                for e in entities
            ]

    def format_recall_context(self, entities: list[dict[str, Any]]) -> str:
        """格式化召回的实体为上下文文本"""
        if not entities:
            return ""

        lines = ["【相关记忆】"]
        for e in entities:
            type_label = {
                "project": "📦 项目",
                "person": "👤 人物",
                "preference": "⚙️ 偏好",
                "topic": "💬 主题",
                "tool": "🔧 工具",
                "device": "🖥️ 设备",
            }.get(e["entity_type"], f"📌 {e['entity_type']}")

            attrs = ""
            if e.get("attributes"):
                attrs = " | " + ", ".join(f"{k}={v}" for k, v in e["attributes"].items())

            lines.append(
                f"{type_label} {e['name']}: {e.get('description', '')}{attrs} "
                f"(提及 {e['mention_count']} 次)"
            )
        return "\n".join(lines)


# 全局单例
_entity_service: EntityService | None = None


def get_entity_service() -> EntityService:
    global _entity_service
    if _entity_service is None:
        _entity_service = EntityService()
    return _entity_service
