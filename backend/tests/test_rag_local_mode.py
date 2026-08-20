"""Local-mode memory + RAG capability (no vector stack by default)."""

from __future__ import annotations

import pytest

from datetime import date

from backend.agent.file_context import load_workspace_memory_bundle
from backend.services.rag.capability import (
    compute_rag_status,
    invalidate_rag_status_cache,
)
from backend.services.rag.factory import RAGServiceFactory
from backend.services.rag.null_impl import NullRAGService


def test_default_local_mode_without_emb_qdrant(monkeypatch):
    invalidate_rag_status_cache()
    from backend.core import config as cfg

    monkeypatch.setattr(cfg.settings, "embedding_provider", "")
    monkeypatch.setattr(cfg.settings, "embedding_model", "")
    monkeypatch.setattr(cfg.settings, "embedding_base_url", "")
    monkeypatch.setattr(cfg.settings, "qdrant_url", "")
    monkeypatch.setattr(cfg.settings, "rag_enabled", True)
    st = compute_rag_status()
    assert st.mode == "local"
    assert st.vector_rag is False
    assert st.auto_inject is False
    assert st.index_upload is False
    assert st.file_memory is True
    assert st.wiki_inject is True


def test_full_mode_when_emb_and_qdrant_configured(monkeypatch):
    invalidate_rag_status_cache()
    from backend.core import config as cfg

    monkeypatch.setattr(cfg.settings, "embedding_provider", "openai-compatible")
    monkeypatch.setattr(cfg.settings, "embedding_model", "embedding-model")
    monkeypatch.setattr(cfg.settings, "embedding_base_url", "http://127.0.0.1:8086")
    monkeypatch.setattr(cfg.settings, "qdrant_url", "http://127.0.0.1:6333")
    monkeypatch.setattr(cfg.settings, "rag_enabled", True)
    monkeypatch.setattr(cfg.settings, "reranker_provider", "")
    st = compute_rag_status()
    assert st.mode == "full"
    assert st.vector_rag is True
    assert st.auto_inject is True
    assert st.reranker_configured is False


def test_force_off_with_rag_enabled_false(monkeypatch):
    invalidate_rag_status_cache()
    from backend.core import config as cfg

    monkeypatch.setattr(cfg.settings, "embedding_provider", "openai-compatible")
    monkeypatch.setattr(cfg.settings, "embedding_model", "m")
    monkeypatch.setattr(cfg.settings, "embedding_base_url", "http://x")
    monkeypatch.setattr(cfg.settings, "qdrant_url", "http://q")
    monkeypatch.setattr(cfg.settings, "rag_enabled", False)
    st = compute_rag_status()
    assert st.mode == "local"
    assert st.vector_rag is False


def test_factory_returns_null_in_local_mode(monkeypatch):
    invalidate_rag_status_cache()
    RAGServiceFactory.reset()
    from backend.core import config as cfg

    monkeypatch.setattr(cfg.settings, "embedding_provider", "")
    monkeypatch.setattr(cfg.settings, "qdrant_url", "")
    svc = RAGServiceFactory.get_service()
    assert isinstance(svc, NullRAGService)


def test_memory_bundle_loads_index_temp_dated(tmp_path):
    (tmp_path / "memory.md").write_text("# Index\nrule-a\n" * 5, encoding="utf-8")
    (tmp_path / "memory_temp.md").write_text("scratch note", encoding="utf-8")
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    today = date.today().isoformat()
    (mem_dir / f"{today}.md").write_text(f"daily {today}", encoding="utf-8")
    block, meta = load_workspace_memory_bundle(extra_roots=[tmp_path])
    assert "memory.md" in block or "Index" in block or "rule-a" in block
    assert "scratch" in block
    assert today in block
    assert meta["memory_md"]
    assert meta["memory_temp"]


@pytest.mark.asyncio
async def test_index_document_text_ready_not_error_when_rag_not_ready(monkeypatch):
    """Builtin seeds / uploads must not become status=error just because embedding is empty."""
    import uuid
    from types import SimpleNamespace

    from backend.core import config as cfg
    from backend.services.knowledge import indexer
    from backend.services.rag.capability import invalidate_rag_status_cache

    invalidate_rag_status_cache()
    monkeypatch.setattr(cfg.settings, "embedding_provider", "")
    monkeypatch.setattr(cfg.settings, "embedding_model", "")
    monkeypatch.setattr(cfg.settings, "embedding_base_url", "")
    monkeypatch.setattr(cfg.settings, "qdrant_url", "")
    monkeypatch.setattr(cfg.settings, "rag_enabled", True)

    seen = {}

    class _Repo:
        def __init__(self, *a, **k):
            pass

        async def update_status(self, document_id, status):
            seen["status"] = status
            seen["id"] = document_id
            return SimpleNamespace(id=document_id, status=status)

    monkeypatch.setattr(indexer, "AsyncDocumentRepository", _Repo)
    result = await indexer.index_document_text(
        document_id=uuid.uuid4(),
        title="seed",
        text="# hello",
        source="builtin-seed",
        skip_wiki=True,
    )
    assert result.get("skipped") is True
    assert result.get("ok") is True
    assert result.get("chunks") == 0
    assert seen.get("status") == "ready"
    assert seen.get("status") != "error"

