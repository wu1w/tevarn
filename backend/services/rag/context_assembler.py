"""
上下文组装器
按 RetrievalContract 策略组装最终注入 LLM 的上下文
支持：阈值过滤、来源加权、时效衰减、去重、Token 预算控制
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backend.core.config import settings
from backend.services.reranker.interface import RerankedResult

logger = logging.getLogger(__name__)


@dataclass
class RetrievalContract:
    """检索契约 — 控制上下文注入策略"""

    min_score: float = 0.5                  # 最低相关度阈值
    max_tokens: int = 4000                  # 上下文 token 预算
    source_weights: dict[str, float] = field(default_factory=lambda: {
        "knowledge": 1.0,
        "wiki": 0.8,
        "session": 0.6,
        "feishu": 0.5,
    })
    recency_decay_days: int = 365           # 时效衰减周期（天）
    recency_decay_factor: float = 0.95      # 每周期衰减因子
    deduplicate: bool = True                # 跨源去重
    include_source_label: bool = True       # 标注来源
    include_timestamp: bool = True          # 标注时间


class ContextAssembler:
    """上下文组装器 — 按 RetrievalContract 组装最终上下文"""

    def __init__(self, contract: RetrievalContract | None = None):
        if contract:
            self.contract = contract
        else:
            # 从 settings 构建
            self.contract = RetrievalContract(
                min_score=getattr(settings, "rag_min_score", 0.5),
                max_tokens=getattr(settings, "rag_max_context_tokens", 4000),
                source_weights=getattr(settings, "rag_source_weights", {
                    "knowledge": 1.0, "wiki": 0.8, "session": 0.6, "feishu": 0.5,
                }),
                deduplicate=getattr(settings, "rag_deduplicate", True),
            )

    def assemble(
        self,
        results: list[RerankedResult],
        metadata: list[dict[str, Any]] | None = None,
    ) -> str:
        """
        组装上下文
        1. 阈值过滤
        2. 来源加权
        3. 时效衰减
        4. 去重
        5. Token 预算控制
        6. 格式化输出
        """
        if not results:
            return ""

        # 补齐 metadata
        if metadata is None:
            metadata = [{}] * len(results)
        # 确保 metadata 长度与 results 一致
        while len(metadata) < len(results):
            metadata.append({})

        # 1. 阈值过滤
        filtered: list[tuple[RerankedResult, dict[str, Any]]] = []
        for r, m in zip(results, metadata):
            if r.score >= self.contract.min_score:
                filtered.append((r, m))

        if not filtered:
            logger.debug(f"All results below min_score={self.contract.min_score}, context empty")
            return ""

        # 2. 来源加权
        for r, m in filtered:
            source = m.get("_source_collection", "knowledge")
            weight = self.contract.source_weights.get(source, 1.0)
            if weight != 1.0:
                r.score = r.score * weight

        # 3. 时效衰减
        if self.contract.recency_decay_days > 0:
            now = datetime.now(timezone.utc)
            for r, m in filtered:
                created_str = m.get("created_at")
                if created_str:
                    try:
                        if isinstance(created_str, str):
                            created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                        elif isinstance(created_str, datetime):
                            created = created_str
                        else:
                            continue
                        age_days = (now - created).days
                        periods = age_days / self.contract.recency_decay_days
                        r.score = r.score * (self.contract.recency_decay_factor ** periods)
                    except Exception:
                        pass

        # 4. 去重（基于 document_id）
        if self.contract.deduplicate:
            seen_docs: set[str] = set()
            deduped: list[tuple[RerankedResult, dict[str, Any]]] = []
            for r, m in filtered:
                doc_id = m.get("document_id", r.text[:50])
                if doc_id not in seen_docs:
                    seen_docs.add(doc_id)
                    deduped.append((r, m))
            filtered = deduped

        # 5. 按加权后分数重新排序
        filtered.sort(key=lambda x: -x[0].score)

        # 6. Token 预算控制 + 格式化输出
        budget = self.contract.max_tokens
        output_parts: list[str] = []
        used_tokens = 0

        for r, m in filtered:
            # 粗略估算：1 token ≈ 3 字符（中文为主）或 4 字符（英文为主）
            est_tokens = max(1, len(r.text) // 3)
            if used_tokens + est_tokens > budget:
                # 尝试截断最后一条
                remaining = budget - used_tokens
                if remaining > 50:
                    truncated_text = r.text[:remaining * 3] + "..."
                    output_parts.append(self._format_item(
                        len(output_parts) + 1, r, m, truncated_text
                    ))
                    used_tokens = budget
                break

            output_parts.append(self._format_item(len(output_parts) + 1, r, m, r.text))
            used_tokens += est_tokens

        if not output_parts:
            return ""

        return "# 检索到的相关知识\n\n" + "\n\n".join(output_parts)

    def _format_item(
        self,
        index: int,
        result: RerankedResult,
        meta: dict[str, Any],
        text: str,
    ) -> str:
        """格式化单条检索结果"""
        parts = [f"## 文档 {index}"]

        # 相关度
        parts.append(f"(相关度: {result.score:.3f})")

        # 来源标签
        if self.contract.include_source_label:
            source = meta.get("_source_collection", "knowledge")
            # 逻辑名映射
            source_labels = {
                "knowledge_base": "知识库",
                "wiki_pages": "Wiki",
                "session_history": "会话记录",
                "feishu_messages": "飞书对话",
                "knowledge": "知识库",
                "wiki": "Wiki",
                "session": "会话记录",
                "feishu": "飞书对话",
            }
            label = source_labels.get(source, source)
            parts.append(f"[{label}]")

        # 时间标签
        if self.contract.include_timestamp:
            created = meta.get("created_at")
            if created:
                try:
                    if isinstance(created, str):
                        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        parts.append(f"({dt.strftime('%Y-%m-%d')})")
                    elif isinstance(created, datetime):
                        parts.append(f"({created.strftime('%Y-%m-%d')})")
                except Exception:
                    pass

        header = " ".join(parts)
        return f"{header}\n{text}"
