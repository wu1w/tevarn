"""编制记忆：注入顺序、experience cap、失败不沉淀、preview、审批门禁。"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.kernel import AgentKernel
from backend.kernel.audit_store import AuditEventStore
from backend.kernel.crew_memory import (
    CrewMemoryAssembler,
    CrewMemoryWriter,
    get_crew_memory_assembler,
)  # CrewMemoryWriter used in vector + retire tests
from backend.kernel.identity import IdentityRegistry


@pytest.fixture()
def env(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/cm.db", future=True)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from backend.models.base import Base
    import backend.models  # noqa: F401

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    store = AuditEventStore(str(tmp_path / "e.jsonl"))
    kernel = AgentKernel(audit_store=store)
    registry = IdentityRegistry(kernel, SessionLocal)
    yield {"registry": registry, "kernel": kernel, "engine": engine}
    asyncio.run(engine.dispose())


def test_sticky_persona_duty_present(env):
    async def go():
        reg = env["registry"]
        ident = await reg.create("mem-a", role="research", capabilities=["web_search"])
        await reg.add_memory(
            ident.id, "persona", "严谨克制", source="system", approved_by="t"
        )
        await reg.add_memory(
            ident.id, "duty", "负责调研", source="system", approved_by="t"
        )
        asm = CrewMemoryAssembler(reg)
        r = await asm.build_inject_block(ident.id, "调研市场", mode="workforce")
        assert "严谨" in r.body or "persona" in r.body
        assert "调研" in r.body or "duty" in r.body
        kinds = {e.kind for e in r.entries_used}
        assert "persona" in kinds or "duty" in kinds

    asyncio.run(go())


def test_experience_cap(env, monkeypatch):
    monkeypatch.setattr(
        "backend.core.config.settings.crew_memory_experience_max_inject", 2, raising=False
    )

    async def go():
        reg = env["registry"]
        ident = await reg.create("mem-b", role="dev", capabilities=["file_rw"])
        await reg.add_memory(
            ident.id, "persona", "务实", source="system", approved_by="t"
        )
        for i in range(10):
            await reg.add_memory(
                ident.id,
                "experience",
                f"经验条目编号 {i} 详细内容足够长" * 3,
                source="system",
            )
        asm = CrewMemoryAssembler(reg)
        r = await asm.build_inject_block(ident.id, "改代码", mode="workforce")
        exp_used = [e for e in r.entries_used if e.kind == "experience"]
        assert len(exp_used) <= 2

    asyncio.run(go())


def test_fail_does_not_distill(env, monkeypatch):
    monkeypatch.setattr(
        "backend.core.config.settings.crew_memory_auto_distill", True, raising=False
    )
    monkeypatch.setattr(
        "backend.core.config.settings.crew_memory_require_approve_distill",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        "backend.core.config.settings.crew_memory_auto_distill_min_chars",
        10,
        raising=False,
    )

    async def go():
        reg = env["registry"]
        ident = await reg.create("mem-c", role="dev", capabilities=["file_rw"])
        before = await reg.current_memory(ident.id, kind="experience")
        writer = CrewMemoryWriter(reg)
        out = await writer.maybe_distill_from_job(
            identity_id=ident.id,
            instruction="大任务",
            result="[Budget Exceeded] 进程 token 预算耗尽",
            status="done",
        )
        assert out is None
        after = await reg.current_memory(ident.id, kind="experience")
        assert len(after) == len(before)

    asyncio.run(go())


def test_preview_stable(env):
    async def go():
        reg = env["registry"]
        ident = await reg.create("mem-d", role="research", capabilities=["web_search"])
        await reg.add_memory(
            ident.id, "persona", "好奇", source="system", approved_by="t"
        )
        await reg.add_memory(
            ident.id, "duty", "检索综合", source="system", approved_by="t"
        )
        asm = get_crew_memory_assembler(reg)
        a = await asm.build_inject_block(ident.id, "foo bar", mode="preview")
        b = await asm.build_inject_block(ident.id, "foo bar", mode="preview")
        assert a.body == b.body
        assert a.header == b.header

    asyncio.run(go())


def test_distilled_requires_approved_by(env):
    async def go():
        reg = env["registry"]
        ident = await reg.create("mem-e", role="dev", capabilities=["file_rw"])
        with pytest.raises(ValueError, match="approved_by"):
            await reg.add_memory(
                ident.id,
                "experience",
                "蒸馏内容",
                source="distilled",
            )

    asyncio.run(go())


def test_manual_force_distill(env, monkeypatch):
    monkeypatch.setattr(
        "backend.core.config.settings.crew_memory_auto_distill", False, raising=False
    )

    async def go():
        reg = env["registry"]
        ident = await reg.create("mem-f", role="dev", capabilities=["file_rw"])
        writer = CrewMemoryWriter(reg)
        # auto 关 + 非 force → 跳过
        skip = await writer.maybe_distill_from_job(
            identity_id=ident.id,
            instruction="任务",
            result="完成了详细结果" * 20,
            status="done",
            force=False,
        )
        assert skip is None
        # force 手动
        entry = await writer.maybe_distill_from_job(
            identity_id=ident.id,
            instruction="任务",
            result="完成了详细结果" * 20,
            status="done",
            force=True,
            approved_by="owner",
        )
        assert entry is not None
        cur = await reg.current_memory(ident.id, kind="experience")
        assert len(cur) >= 1

    asyncio.run(go())


def test_retire_tombstone_not_injected(env):
    async def go():
        reg = env["registry"]
        ident = await reg.create("mem-g", role="dev", capabilities=["file_rw"])
        e = await reg.add_memory(
            ident.id, "experience", "应被废止的旧经验", source="system"
        )
        writer = CrewMemoryWriter(reg)
        await writer.retire(e.id, approved_by="owner")
        asm = CrewMemoryAssembler(reg)
        r = await asm.build_inject_block(ident.id, "x", mode="workforce")
        assert "应被废止" not in r.body

    asyncio.run(go())


def test_experience_vector_topk_aligned_to_sqlite(env, monkeypatch):
    """向量命中后只注入 SQLite current 中仍生效的 experience（非 tombstone）。"""

    async def go():
        reg = env["registry"]
        ident = await reg.create("mem-vec", role="dev", capabilities=["file_rw"])
        await reg.add_memory(
            ident.id, "persona", "务实", source="system", approved_by="t"
        )
        entries = []
        for i, topic in enumerate(
            [
                "数据库迁移与 schema 变更经验",
                "前端样式与 CSS 布局技巧",
                "Kubernetes 集群排障手册",
                "数据库索引优化与慢查询",
                "UI 动效与动画性能",
            ]
        ):
            e = await reg.add_memory(
                ident.id,
                "experience",
                f"经验{i}: {topic} " + ("详" * 40),
                source="system",
            )
            entries.append(e)

        # 模拟向量返回：优先「数据库」两条（乱序 id）
        db_ids = [str(entries[0].id), str(entries[3].id)]
        css_ids = [str(entries[1].id)]

        class _Doc:
            def __init__(self, eid, kind="experience", text=""):
                self.id = eid
                self.payload = {"entry_id": eid, "kind": kind, "text": text}
                self.text = text

        class _FakeRAG:
            async def search_identity_memory(self, query, identity_id, top_k=8):
                # 查询含数据库 → 返回 db 相关
                ordered = db_ids + css_ids
                return [_Doc(i) for i in ordered[:top_k]]

        monkeypatch.setattr(
            "backend.services.rag.capability.use_vector_rag",
            lambda: True,
        )

        class _Fac:
            @staticmethod
            def get_service():
                return _FakeRAG()

        monkeypatch.setattr(
            "backend.services.rag.factory.RAGServiceFactory",
            _Fac,
        )
        monkeypatch.setattr(
            "backend.core.config.settings.crew_memory_experience_max_inject",
            2,
            raising=False,
        )

        asm = CrewMemoryAssembler(reg)
        r = await asm.build_inject_block(
            ident.id, "帮我优化数据库索引", mode="workforce"
        )
        exp_used = [e for e in r.entries_used if e.kind == "experience"]
        assert len(exp_used) == 2
        used_ids = {e.id for e in exp_used}
        assert used_ids.issubset(set(db_ids))
        assert "数据库" in r.body or "索引" in r.body or "schema" in r.body.lower()
        # 不应把 CSS 当首选（向量把 db 排前）
        assert "CSS" not in r.body or "数据库" in r.body

        # tombstone 后向量仍返回该 id → 不得注入
        await CrewMemoryWriter(reg).retire(entries[0].id, approved_by="t")
        r2 = await asm.build_inject_block(
            ident.id, "帮我优化数据库索引", mode="workforce"
        )
        used2 = {e.id for e in r2.entries_used if e.kind == "experience"}
        assert str(entries[0].id) not in used2

    asyncio.run(go())


def test_dispatcher_delegates_assembler(env):
    async def go():
        from backend.kernel.dispatcher import WorkforceDispatcher
        from backend.kernel.inbox import InboxService

        reg = env["registry"]
        kernel = env["kernel"]
        # minimal inbox with same session factory
        from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

        # reuse registry session via create path
        ident = await reg.create("mem-h", role="dev", capabilities=["file_rw"])
        await reg.add_memory(
            ident.id, "persona", "简洁", source="system", approved_by="t"
        )
        # WorkforceDispatcher needs inbox — use a stub session factory from registry
        SessionLocal = reg._session_factory
        inbox = InboxService(kernel, SessionLocal, max_pending=20)
        disp = WorkforceDispatcher(kernel, inbox, reg, SessionLocal)
        header, body = await disp._build_memory_block(
            ident, "指令", await reg.current_memory(ident.id)
        )
        assert "身份记忆" in header or "记忆" in header
        assert "简洁" in body

    asyncio.run(go())
