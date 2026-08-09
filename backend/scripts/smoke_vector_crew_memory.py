"""冒烟：远程 Embedding + Qdrant +（可选）Reranker 下编制记忆向量 top-k。

用法（本机）::

    set TEVARN_EMBEDDING_PROVIDER=openai-compatible
    set TEVARN_EMBEDDING_BASE_URL=http://192.168.5.27:8086
    set TEVARN_EMBEDDING_MODEL=Qwen3-Embedding-4B
    set TEVARN_QDRANT_URL=http://192.168.5.27:6333
    set TEVARN_RERANKER_PROVIDER=local
    set TEVARN_RERANKER_BASE_URL=http://192.168.5.27:8087
    set TEVARN_RERANKER_MODEL=Qwen3-Reranker-4B
    set TEVARN_RAG_ENABLED=true
    python -m backend.scripts.smoke_vector_crew_memory

不写入仓库密钥；仅联调。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path


def _ensure_env_defaults() -> None:
    """若未设置，填入局域网默认（可被环境变量覆盖）。"""
    defaults = {
        "TEVARN_EMBEDDING_PROVIDER": "openai-compatible",
        "TEVARN_EMBEDDING_BASE_URL": "http://192.168.5.27:8086",
        "TEVARN_EMBEDDING_MODEL": "Qwen3-Embedding-4B",
        # Qwen3-Embedding-4B 实测 2560 维；建 collection 必须对齐
        "TEVARN_EMBEDDING_DIMENSIONS": "2560",
        "TEVARN_QDRANT_URL": "http://192.168.5.27:6333",
        "TEVARN_RERANKER_PROVIDER": "local",
        "TEVARN_RERANKER_BASE_URL": "http://192.168.5.27:8087",
        "TEVARN_RERANKER_MODEL": "Qwen3-Reranker-4B",
        "TEVARN_RAG_ENABLED": "true",
        # 避免 smoke 被弱密钥校验挡住
        "TEVARN_JWT_SECRET": os.environ.get(
            "TEVARN_JWT_SECRET", "dev-jwt-secret-for-local-alpha-testing-only-32b"
        ),
        "TEVARN_API_KEY": os.environ.get(
            "TEVARN_API_KEY", "dev-api-key-for-local-alpha-testing-only-32bxx"
        ),
        "TEVARN_SETTINGS_ENCRYPTION_SALT": os.environ.get(
            "TEVARN_SETTINGS_ENCRYPTION_SALT", "local-dev-salt-not-for-prod-use-xx"
        ),
    }
    for k, v in defaults.items():
        os.environ.setdefault(k, v)


async def main() -> int:
    _ensure_env_defaults()

    # 必须在 import settings 之前设 env
    from backend.core.config import settings
    from backend.services.embedding.factory import EmbeddingServiceFactory
    from backend.services.rag.capability import (
        get_rag_status,
        invalidate_rag_status_cache,
        use_vector_rag,
    )
    from backend.services.rag.factory import RAGServiceFactory
    from backend.services.reranker.factory import RerankerServiceFactory

    invalidate_rag_status_cache()
    EmbeddingServiceFactory.reset()
    RAGServiceFactory.reset() if hasattr(RAGServiceFactory, "reset") else None
    try:
        RerankerServiceFactory.reset()
    except Exception:
        pass

    st = get_rag_status(force=True)
    print("=== RAG status ===")
    print(st.to_dict())
    if not use_vector_rag():
        print("FAIL: vector_rag not ready")
        return 2

    emb = EmbeddingServiceFactory.get_service()
    vec = await emb.embed_query("数据库索引优化")
    print(f"embed ok dim={len(vec)} provider={settings.embedding_provider} model={settings.embedding_model}")
    if not vec or len(vec) < 8:
        print("FAIL: empty embedding")
        return 3

    # reranker optional smoke
    try:
        rr = RerankerServiceFactory.get_service()
        ranked = await rr.rerank(
            "数据库",
            ["数据库索引优化经验", "CSS 布局技巧", "K8s 排障"],
            top_n=2,
        )
        print(
            "rerank ok:",
            [(getattr(x, "index", None), round(float(getattr(x, "score", 0)), 4)) for x in (ranked or [])],
        )
    except Exception as e:
        print("rerank skip/warn:", e)

    rag = RAGServiceFactory.get_service()
    print("rag class:", type(rag).__name__)

    # 临时 DB + Identity 写入 + 向量索引 + Assembler
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    import backend.models  # noqa: F401
    from backend.kernel import AgentKernel
    from backend.kernel.audit_store import AuditEventStore
    from backend.kernel.crew_memory import CrewMemoryAssembler
    from backend.kernel.identity import IdentityRegistry
    from backend.models.base import Base

    tmp = Path(tempfile.mkdtemp(prefix="tevarn-vec-smoke-"))
    db = tmp / "smoke.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}", future=True)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    store = AuditEventStore(str(tmp / "e.jsonl"))
    kernel = AgentKernel(audit_store=store)
    reg = IdentityRegistry(kernel, SessionLocal)

    ident = await reg.create("向量测工", role="dba", capabilities=["file_rw"])
    await reg.add_memory(
        ident.id, "persona", "严谨的数据库专家", source="system", approved_by="smoke"
    )
    topics = [
        "PostgreSQL 索引优化与 EXPLAIN 分析经验",
        "前端 React CSS 模块化样式规范",
        "MySQL 慢查询日志与覆盖索引实践",
        "Kubernetes Pod 崩溃排查步骤",
        "数据库备份恢复与 point-in-time 恢复",
        "Tailwind 动画与响应式布局技巧",
        "SQLite 事务隔离与 WAL 模式",
        "Redis 缓存击穿与热点 key 治理",
    ]
    written = []
    for t in topics:
        e = await reg.add_memory(ident.id, "experience", t + "。" + ("细节" * 20), source="system")
        written.append(e)
        # 显式确保索引（add_memory 已 best-effort；再刷一次）
        ok = await rag.upsert_identity_memory(
            entry_id=str(e.id),
            identity_id=str(ident.id),
            kind="experience",
            content=str(e.content),
            version=int(e.version or 1),
        )
        print(f"  upsert {str(e.id)[:8]} ok={ok} :: {t[:28]}")

    # 等索引落盘
    await asyncio.sleep(0.5)

    docs = await rag.search_identity_memory(
        "如何优化数据库索引与慢查询", str(ident.id), top_k=5
    )
    print(f"search hits={len(docs)}")
    for d in docs:
        kind = (d.payload or {}).get("kind")
        print(f"  score={d.score:.4f} kind={kind} text={(d.text or '')[:60]}")

    if not docs:
        print("FAIL: search_identity_memory empty")
        await engine.dispose()
        return 4

    asm = CrewMemoryAssembler(reg)
    # force cap=2
    os.environ["TEVARN_CREW_MEMORY_EXPERIENCE_MAX_INJECT"] = "2"
    # settings already loaded — patch attribute
    try:
        object.__setattr__(settings, "crew_memory_experience_max_inject", 2)
    except Exception:
        settings.crew_memory_experience_max_inject = 2  # type: ignore[attr-defined]

    result = await asm.build_inject_block(
        ident.id,
        "请帮我做数据库索引优化，分析慢查询",
        mode="workforce",
    )
    exp_used = [e for e in result.entries_used if e.kind == "experience"]
    print("=== Assembler inject ===")
    print("mode", result.mode, "truncated", result.truncated, "token_est", result.token_estimate)
    print("experience used", len(exp_used), [e.id[:8] for e in exp_used])
    print(result.body[:800])

    db_signals = any(
        k in result.body
        for k in ("索引", "数据库", "PostgreSQL", "MySQL", "SQLite", "慢查询", "备份")
    )
    if len(exp_used) > 2:
        print("FAIL: experience over cap")
        await engine.dispose()
        return 5
    if not db_signals and len(exp_used) > 0:
        print("WARN: inject body may not prefer DB topics (still ok if vector weak)")
    else:
        print("OK: vector-aligned inject looks reasonable")

    # cleanup points best-effort
    for e in written:
        try:
            await rag.delete_identity_memory(str(e.id))
        except Exception:
            pass

    await engine.dispose()
    print("PASS smoke_vector_crew_memory")
    return 0


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
    except Exception as e:
        print("ERROR", e)
        raise
    sys.exit(rc)
