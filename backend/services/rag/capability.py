"""
RAG / 本地检索能力门面（Claude Code 风格：无向量时为默认主路径）。

full 向量 RAG 仅当 Embedding + Qdrant 均已配置且可用意图开启时生效。
Reranker 仅为增强项，不参与就绪判定。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from backend.core.config import settings

logger = logging.getLogger(__name__)

Mode = Literal["local", "full"]

_CACHE: dict[str, Any] = {"ts": 0.0, "status": None}
_CACHE_TTL = 30.0


@dataclass
class RagStatus:
    """运行时能力快照。"""

    mode: Mode = "local"
    # 配置是否齐全（不保证网络通）
    embedding_configured: bool = False
    qdrant_configured: bool = False
    reranker_configured: bool = False
    rag_enabled: bool = False
    # 有效能力
    vector_rag: bool = False  # auto-inject + 向量 skill
    auto_inject: bool = False
    tool_search: bool = False
    index_upload: bool = False
    wiki_inject: bool = True
    file_memory: bool = True
    reason: str = ""
    hints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "embedding_configured": self.embedding_configured,
            "qdrant_configured": self.qdrant_configured,
            "reranker_configured": self.reranker_configured,
            "rag_enabled": self.rag_enabled,
            "vector_rag": self.vector_rag,
            "auto_inject": self.auto_inject,
            "tool_search": self.tool_search,
            "index_upload": self.index_upload,
            "wiki_inject": self.wiki_inject,
            "file_memory": self.file_memory,
            "reason": self.reason,
            "hints": list(self.hints),
        }


def _has_embedding_config() -> bool:
    provider = (getattr(settings, "embedding_provider", None) or "").strip()
    model = (getattr(settings, "embedding_model", None) or "").strip()
    base = (getattr(settings, "embedding_base_url", None) or "").strip()
    # 需要 provider，且 model 或 base_url 至少一个
    if not provider:
        return False
    return bool(model or base)


def _has_qdrant_config() -> bool:
    url = (getattr(settings, "qdrant_url", None) or "").strip()
    if not url:
        return False
    # 占位默认也不算“用户已配置就绪意图”？用户可能仍用 localhost
    # 按“填写了非空 URL”即 configured；真正连通在 probe 时再判
    return True


def _has_reranker_config() -> bool:
    provider = (getattr(settings, "reranker_provider", None) or "").strip()
    if not provider:
        return False
    model = (getattr(settings, "reranker_model", None) or "").strip()
    base = (getattr(settings, "reranker_base_url", None) or "").strip()
    return bool(model or base)


def compute_rag_status() -> RagStatus:
    emb = _has_embedding_config()
    qd = _has_qdrant_config()
    rr = _has_reranker_config()
    # 总开关：默认 True=允许在栈就绪时启用；False=强制关闭向量 RAG
    flag = bool(getattr(settings, "rag_enabled", True))

    st = RagStatus(
        embedding_configured=emb,
        qdrant_configured=qd,
        reranker_configured=rr,
        rag_enabled=flag,
        wiki_inject=True,
        file_memory=True,
    )

    if not flag:
        st.mode = "local"
        st.vector_rag = False
        st.reason = "向量 RAG 已关闭（rag_enabled=false），使用本地 memory + Wiki + 对话上下文"
        st.hints = ["在设置中开启「会话自动检索 / rag_enabled」并配置 Embedding 与 Qdrant 以启用向量 RAG"]
        return st

    if emb and qd:
        st.mode = "full"
        st.vector_rag = True
        st.auto_inject = True
        st.tool_search = True
        st.index_upload = True
        st.reason = "Embedding + Qdrant 已配置，向量 RAG 已启用"
        if not rr:
            st.hints = ["Reranker 未配置：检索仍可用，精排将回退到向量分（可选增强）"]
        else:
            st.hints = ["Reranker 已配置，将用于精排增强"]
        return st

    # 默认：本地模式（Claude Code 风格）
    st.mode = "local"
    st.vector_rag = False
    st.auto_inject = False
    st.tool_search = False
    st.index_upload = False
    missing = []
    if not emb:
        missing.append("Embedding（provider + model/url）")
    if not qd:
        missing.append("Qdrant URL")
    st.reason = (
        "当前为本地模式（默认）：使用 memory.md / memory_temp / 日期短记忆 + Wiki + 上下文压缩。"
        f"未启用向量 RAG，缺少：{'、'.join(missing) if missing else '配置'}"
    )
    st.hints = [
        "在设置中配置 Embedding 与 Qdrant 后自动启用完整 RAG",
        "Reranker 为可选项，仅增强精排效果",
        "工作区可维护 memory.md、memory_temp.md、memory/YYYY-MM-DD.md",
    ]
    return st


def get_rag_status(*, force: bool = False) -> RagStatus:
    now = time.monotonic()
    if (
        not force
        and _CACHE["status"] is not None
        and (now - float(_CACHE["ts"])) < _CACHE_TTL
    ):
        return _CACHE["status"]  # type: ignore[return-value]
    st = compute_rag_status()
    _CACHE["ts"] = now
    _CACHE["status"] = st
    return st


def invalidate_rag_status_cache() -> None:
    _CACHE["ts"] = 0.0
    _CACHE["status"] = None


def use_vector_rag() -> bool:
    return get_rag_status().vector_rag
