"""
将数据库中的运行时设置同步到内存中的 Settings 单例，并刷新 LLM/Embedding 等工厂。

桌面用户通过「设置」页修改的是 DB 配置；若不落到 pydantic settings，
LLMServiceFactory 仍会一直使用启动时的环境变量默认值。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from backend.core.config import settings

logger = logging.getLogger(__name__)

# DB key -> Settings 字段名（同名时可省略，这里显式列出便于维护）
_KEY_MAP: dict[str, str] = {
    "llm_provider": "llm_provider",
    "llm_model": "llm_model",
    "llm_base_url": "llm_base_url",
    "llm_api_key": "llm_api_key",
    "max_tokens": "default_max_tokens",
    "temperature": "llm_temperature",
    "context_window": "context_window",
    "context_threshold_percent": "context_threshold_percent",
    "context_protect_first_n": "context_protect_first_n",
    "context_protect_last_n": "context_protect_last_n",
    "context_max_tool_output_chars": "context_max_tool_output_chars",
    "context_enable_l1": "context_enable_l1",
    "context_enable_l3": "context_enable_l3",
    "context_enable_l5": "context_enable_l5",
    "context_compress_model": "context_compress_model",
    "embedding_provider": "embedding_provider",
    "embedding_model": "embedding_model",
    "embedding_base_url": "embedding_base_url",
    "embedding_api_key": "embedding_api_key",
    "reranker_provider": "reranker_provider",
    "reranker_model": "reranker_model",
    "reranker_base_url": "reranker_base_url",
    "reranker_api_key": "reranker_api_key",
    "image_provider": "image_provider",
    "image_model": "image_model",
    "image_base_url": "image_base_url",
    "image_api_key": "image_api_key",
    "qdrant_url": "qdrant_url",
    "qdrant_collection": "qdrant_collection",
    "rag_enabled": "rag_enabled",
}

# 这些 key 变更后需要重建对应服务单例
_LLM_KEYS = {
    "llm_provider",
    "llm_model",
    "llm_base_url",
    "llm_api_key",
    "max_tokens",
    "temperature",
    "context_window",
}
_EMBED_KEYS = {
    "embedding_provider",
    "embedding_model",
    "embedding_base_url",
    "embedding_api_key",
}
_RERANK_KEYS = {
    "reranker_provider",
    "reranker_model",
    "reranker_base_url",
    "reranker_api_key",
}
_RAG_KEYS = {
    "qdrant_url",
    "qdrant_collection",
    "rag_enabled",
}
_IMAGE_KEYS = {
    "image_provider",
    "image_model",
    "image_base_url",
    "image_api_key",
}


def _coerce(attr: str, value: Any) -> Any:
    """按当前字段类型做简单转换。"""
    current = getattr(settings, attr, None)
    if value is None:
        return value
    if isinstance(current, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if isinstance(current, int) and not isinstance(current, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            return current
    if isinstance(current, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return current
    return value


def apply_setting_value(key: str, value: Any) -> bool:
    """应用单个配置到内存 settings。返回是否识别并写入。"""
    attr = _KEY_MAP.get(key)
    if not attr or not hasattr(settings, attr):
        return False
    # 脱敏占位符（前端未改 Key 时可能回传 sk-xx...yyyy）不覆盖真值
    if key.endswith("_api_key") and isinstance(value, str):
        if not value or "..." in value or value == "***":
            return False
    try:
        setattr(settings, attr, _coerce(attr, value))
        return True
    except Exception as e:
        logger.warning("Failed to apply setting %s: %s", key, e)
        return False


def reset_factories_for_keys(keys: Iterable[str]) -> None:
    """按变更的 key 重置相关服务工厂。"""
    key_set = set(keys)
    try:
        if key_set & _LLM_KEYS:
            from backend.services.llm import LLMServiceFactory

            LLMServiceFactory.reset()
            logger.info(
                "LLM factory reset → provider=%s model=%s",
                settings.llm_provider,
                settings.llm_model,
            )
        if key_set & _EMBED_KEYS:
            from backend.services.embedding.factory import EmbeddingServiceFactory

            EmbeddingServiceFactory.reset()
            logger.info("Embedding factory reset → %s", settings.embedding_provider)
        if key_set & _RERANK_KEYS:
            from backend.services.reranker.factory import RerankerServiceFactory

            RerankerServiceFactory.reset()
            logger.info("Reranker factory reset")
        if key_set & _IMAGE_KEYS:
            from backend.services.image.factory import ImageGenerationServiceFactory

            ImageGenerationServiceFactory.reset()
            logger.info("Image factory reset")
        if key_set & _RAG_KEYS:
            from backend.services.rag.factory import RAGServiceFactory

            if hasattr(RAGServiceFactory, "reset"):
                RAGServiceFactory.reset()
            logger.info("RAG factory reset → qdrant=%s", getattr(settings, "qdrant_url", ""))
    except Exception as e:
        logger.warning("Factory reset failed: %s", e)


def apply_settings_dict(items: dict[str, Any], *, reset: bool = True) -> list[str]:
    """批量应用 {key: value}，返回实际写入的 key 列表。"""
    applied: list[str] = []
    for key, value in items.items():
        if apply_setting_value(key, value):
            applied.append(key)
    if reset and applied:
        reset_factories_for_keys(applied)
    return applied


async def load_settings_from_db() -> list[str]:
    """启动时从 DB 加载全部运行时配置。"""
    from backend.repositories.setting_repo import AsyncSettingRepository

    repo = AsyncSettingRepository()
    rows = await repo.list_all() or []
    payload = {r.key: r.value for r in rows}
    applied = apply_settings_dict(payload, reset=True)
    logger.info("Loaded %d runtime settings from DB", len(applied))
    return applied
