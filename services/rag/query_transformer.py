"""
查询变换器
优化检索质量：查询扩展（规则驱动）+ 查询拆解（LLM 驱动）+ HyDE
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from backend.core.config import settings

logger = logging.getLogger(__name__)

# ─── 规则驱动查询扩展 ───

# 中英互译映射（IT/运维/AI 领域高频词）
_ZH_EN_MAP: dict[str, str] = {
    "风扇": "fan",
    "调速": "speed control throttle",
    "温度": "temperature thermal",
    "显卡": "GPU graphics card",
    "内存": "memory RAM",
    "硬盘": "disk storage",
    "网络": "network",
    "配置": "config configuration",
    "部署": "deploy deployment",
    "容器": "container docker",
    "服务": "service",
    "数据库": "database DB",
    "缓存": "cache",
    "负载": "load balance",
    "安全": "security",
    "加密": "encryption",
    "认证": "authentication auth",
    "日志": "log logging",
    "监控": "monitor monitoring",
    "报错": "error exception",
    "超时": "timeout",
    "重启": "restart reboot",
    "升级": "upgrade update",
    "回滚": "rollback revert",
    "备份": "backup",
    "恢复": "restore recovery",
}

# 缩写展开映射
_ABBR_MAP: dict[str, str] = {
    "OOM": "Out of Memory",
    "GPU": "Graphics Processing Unit",
    "CPU": "Central Processing Unit",
    "API": "Application Programming Interface",
    "SSR": "Server-Side Rendering",
    "CSR": "Client-Side Rendering",
    "RAG": "Retrieval Augmented Generation",
    "LLM": "Large Language Model",
    "RCE": "Remote Code Execution",
    "XSS": "Cross-Site Scripting",
    "CSRF": "Cross-Site Request Forgery",
    "SQLi": "SQL Injection",
    "CVE": "Common Vulnerabilities and Exposures",
    "CLI": "Command Line Interface",
    "SDK": "Software Development Kit",
    "ORM": "Object Relational Mapping",
    "CRUD": "Create Read Update Delete",
    "JWT": "JSON Web Token",
    "HTTP": "HyperText Transfer Protocol",
    "HTTPS": "HTTP Secure",
    "SSH": "Secure Shell",
    "TLS": "Transport Layer Security",
    "DNS": "Domain Name System",
    "CDN": "Content Delivery Network",
}


class QueryTransformer:
    """
    查询变换器 — 优化检索质量
    - expand_query(): 规则驱动查询扩展（中英互译 + 缩写展开）
    - decompose_query(): LLM 驱动查询拆解（多意图问题拆子查询）
    - hyde_embed(): HyDE 假设性文档嵌入
    """

    def __init__(self, llm_client: Any = None, embedding_service: Any = None):
        self.llm = llm_client
        self.embedding_service = embedding_service

    async def expand_query(self, query: str) -> list[str]:
        """
        规则驱动查询扩展：生成同义/相关查询
        无需 LLM，零额外开销
        """
        if not getattr(settings, "rag_query_expansion", True):
            return [query]

        expanded = [query]
        additions: list[str] = []

        # 1. 中英互译扩展
        for zh, en in _ZH_EN_MAP.items():
            if zh in query and en not in query:
                additions.append(query.replace(zh, en))
                break  # 每次只替换一个，避免组合爆炸

        # 2. 缩写展开
        for abbr, full in _ABBR_MAP.items():
            # 全词匹配（避免 GPU 匹配到 GPUd 之类）
            pattern = r'\b' + re.escape(abbr) + r'\b'
            if re.search(pattern, query, re.IGNORECASE):
                expanded_version = re.sub(pattern, full, query, flags=re.IGNORECASE)
                if expanded_version != query:
                    additions.append(expanded_version)
                break

        # 去重 + 限制数量
        expanded.extend(additions)
        seen = set()
        unique = []
        for q in expanded:
            q_lower = q.lower().strip()
            if q_lower not in seen:
                seen.add(q_lower)
                unique.append(q)

        return unique[:4]  # 最多 4 个扩展查询

    async def decompose_query(self, query: str) -> list[str]:
        """
        复杂查询拆解（需要 LLM）
        如果 LLM 不可用或未启用，返回原始查询
        """
        if not getattr(settings, "rag_decompose_enabled", False):
            return [query]

        if not self.llm:
            return [query]

        try:
            prompt = (
                "分析以下查询是否包含多个独立问题。如果是，拆解为子查询；如果不是，返回原查询。\n"
                f"查询: {query}\n"
                '输出 JSON: {"needs_decompose": bool, "sub_queries": [...]}\n'
                "只输出 JSON，不要其他内容。"
            )
            response = await self._call_llm(prompt)
            if response:
                # 解析 JSON
                text = response.strip()
                # 尝试从 markdown code block 中提取
                if "```" in text:
                    match = re.search(r'```(?:json)?\s*(.*?)```', text, re.DOTALL)
                    if match:
                        text = match.group(1).strip()

                data = json.loads(text)
                if data.get("needs_decompose") and data.get("sub_queries"):
                    sub_queries = data["sub_queries"]
                    if isinstance(sub_queries, list) and len(sub_queries) > 0:
                        logger.info(f"Query decomposed: '{query[:50]}' → {len(sub_queries)} sub-queries")
                        return sub_queries
        except Exception as e:
            logger.warning(f"Query decomposition failed: {e}")

        return [query]

    async def hyde_embed(self, query: str) -> list[float] | None:
        """
        HyDE (Hypothetical Document Embedding):
        先让 LLM 生成假设性答案，再对答案做 embedding
        检索质量提升 15-30%（论文数据）
        如果 LLM 不可用，返回 None（退回原始查询 embedding）
        """
        if not getattr(settings, "rag_hyde_enabled", False):
            return None

        if not self.llm or not self.embedding_service:
            return None

        try:
            prompt = f"简要回答以下问题（50字以内）：{query}"
            hypothetical = await self._call_llm(prompt)
            if hypothetical and hypothetical.strip():
                vector = await self.embedding_service.embed_query(hypothetical.strip())
                logger.info(f"HyDE: query='{query[:30]}' → hypo='{hypothetical[:30]}' → embed")
                return vector
        except Exception as e:
            logger.warning(f"HyDE embed failed: {e}")

        return None

    async def _call_llm(self, prompt: str) -> str | None:
        """调用 LLM（通用接口）"""
        if not self.llm:
            return None

        try:
            # 尝试不同 LLM 客户端接口
            if hasattr(self.llm, "achat"):
                # Ollama 风格
                result = await self.llm.achat(prompt)
                return str(result) if result else None
            elif hasattr(self.llm, "generate"):
                # 通用 generate
                result = await self.llm.generate(prompt)
                return str(result) if result else None
            elif hasattr(self.llm, "invoke"):
                # LangChain 风格
                result = self.llm.invoke(prompt)
                return str(result) if result else None
        except Exception as e:
            logger.warning(f"LLM call failed: {e}")

        return None
