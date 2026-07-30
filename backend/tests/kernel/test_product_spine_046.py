"""0.4.6 Product Spine：招人 → 派活 → mock 执行 → 结果回写（无真 LLM）。

验收主路径骨架（C1）：
1. Hire 必落 Identity，可选技能包 1:1 挂接
2. 空指令 / 停职拒收有明确错误语义
3. Dispatcher + mock executor 走完 pending→done
4. HTTP 层：人话 400/503（在 workforce 已装配时）
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.kernel import AgentKernel
from backend.kernel.audit_store import AuditEventStore
from backend.kernel.dispatcher import WorkforceDispatcher
from backend.kernel.identity import IdentityRegistry
from backend.kernel.inbox import InboxService


@pytest.fixture()
def spine(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/spine.db", future=True)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    import backend.models  # noqa: F401
    from backend.models.base import Base

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_init())
    store = AuditEventStore(str(tmp_path / "events.jsonl"))
    kernel = AgentKernel(audit_store=store)
    registry = IdentityRegistry(kernel, SessionLocal)
    inbox = InboxService(kernel, SessionLocal, max_pending=50)
    yield {
        "kernel": kernel,
        "registry": registry,
        "inbox": inbox,
        "SessionLocal": SessionLocal,
        "store": store,
    }
    asyncio.run(engine.dispose())


def _run(coro):
    return asyncio.run(coro)


# ── Hire → Identity ──────────────────────────────────────────


def test_hire_creates_identity_and_skill_pack(spine) -> None:
    """B1：招聘结果必落 Identity；技能包 SubAgent 1:1 挂 sub_agent_id。"""

    async def go():
        from backend.models.sub_agent import SubAgent

        reg = spine["registry"]
        # 在本 fixture SQLite 内创建技能包（模拟 API create_skill_pack）
        async with spine["SessionLocal"]() as session:
            pack = SubAgent(
                name="小研",
                description="研究助理",
                icon="👤",
                model_ref="default",
                system_prompt="You are 小研, a research assistant.",
                enabled_toolsets=["file_read", "web_search"],
                max_iterations=20,
                temperature=0.3,
                enabled=True,
                is_builtin=False,
            )
            session.add(pack)
            await session.commit()
            await session.refresh(pack)
            pack_id = pack.id

        ident = await reg.create(
            "小研",
            role="研究助理",
            capabilities=["file_read", "web_search"],
            default_token_budget=30_000,
            sub_agent_id=pack_id,
            meta={"source": "hire_wizard", "persona": "克制专业"},
        )
        assert ident.status == "active"
        assert str(ident.sub_agent_id) == str(pack_id)
        assert (ident.meta or {}).get("source") == "hire_wizard"

        await reg.add_memory(ident.id, "persona", "克制专业", source="system", approved_by="test")
        await reg.add_memory(ident.id, "duty", "行业调研", source="system", approved_by="test")
        mem = await reg.current_memory(ident.id)
        kinds = {m.kind for m in mem}
        assert "persona" in kinds and "duty" in kinds

    _run(go())


def test_hire_name_unique(spine) -> None:
    async def go():
        reg = spine["registry"]
        await reg.create("重名员工", capabilities=["file_read"])
        with pytest.raises(ValueError, match="已存在"):
            await reg.create("重名员工", capabilities=["file_read"])

    _run(go())


# ── 派活错误语义 ─────────────────────────────────────────────


def test_enqueue_rejects_empty_and_suspended(spine) -> None:
    async def go():
        reg, inbox = spine["registry"], spine["inbox"]
        ident = await reg.create("接单员", capabilities=["file_read"])

        with pytest.raises(ValueError, match="instruction"):
            await inbox.enqueue(ident.id, "   ")

        await reg.suspend(ident.id, by="boss")
        rejected = await inbox.enqueue(ident.id, "停职后派活")
        assert rejected is None
        kinds = [e.kind for e in spine["kernel"].events()]
        assert "inbox_dropped" in kinds

    _run(go())


# ── 主路径闭环（mock executor，无 LLM）──────────────────────


def test_hire_dispatch_done_with_mock_executor(spine) -> None:
    """C1 主路径：入编 → 派工单 → dispatcher mock 执行 → done。"""

    async def go():
        reg, inbox, kernel = spine["registry"], spine["inbox"], spine["kernel"]
        ident = await reg.create(
            "流水线员工",
            role="执行测试",
            capabilities=["file_read"],
            default_token_budget=5000,
            meta={"source": "hire_wizard"},
        )
        item = await inbox.enqueue(
            ident.id,
            "请用一句话汇报：主路径烟雾测试通过",
            source="manual",
            priority=5,
        )
        assert item is not None and item.status == "pending"

        async def mock_executor(identity, it, proc_id, k):
            # 模拟工具权限检查 + 轻量扣费（无 LLM）
            await k.mediate(proc_id, "tool_call", "file_read")
            k.charge_tokens(proc_id, 10)
            return f"OK · 员工={identity.name} · 指令已执行"

        disp = WorkforceDispatcher(
            kernel, inbox, reg, spine["SessionLocal"], executor=mock_executor
        )
        n = await disp.tick(wait=True)
        assert n == 1

        done = await inbox.list_items(status="done")
        assert len(done) == 1
        assert "主路径" in done[0].instruction or "烟雾" in done[0].instruction
        assert "OK" in (done[0].result or "")
        assert done[0].process_id
        proc = kernel.get_process(done[0].process_id)
        assert proc is not None
        assert proc.state == "completed"
        assert proc.tokens_used == 10
        ok, _ = kernel.verify_event_chain()
        assert ok

    _run(go())


def test_dispatch_empty_inbox_is_zero(spine) -> None:
    async def go():
        reg, inbox, kernel = spine["registry"], spine["inbox"], spine["kernel"]
        disp = WorkforceDispatcher(
            kernel, inbox, reg, spine["SessionLocal"],
            executor=lambda *a, **k: asyncio.sleep(0),
        )
        n = await disp.tick(wait=True)
        assert n == 0

    _run(go())


# ── HTTP 人话错误（装配 workforce 后）────────────────────────


def test_http_inbox_human_errors(spine, monkeypatch) -> None:
    """API 层：未选员工 / 空指令 / 服务未启 返回可理解 detail。"""
    from fastapi.testclient import TestClient

    from backend.kernel import get_kernel, reset_kernel_for_tests
    from backend.kernel import workforce as wf_mod
    from backend.main import app

    reset_kernel_for_tests()
    wf_mod.reset_workforce_for_tests()

    # 强制未装配 → 503 人话
    client = TestClient(app)
    r = client.post(
        "/api/kernel/inbox",
        json={"identity_id": str(uuid.uuid4()), "instruction": "x"},
    )
    # single_user 可能 200 路径前先鉴权；无 token 时 single_user 仍可过
    if r.status_code == 503:
        assert "收件箱" in r.json()["detail"] or "派活" in r.json()["detail"] or "未启用" in r.json()["detail"]
    elif r.status_code in (401, 403):
        pytest.skip("auth required in this env")
    else:
        # 若 lifespan 已装配 inbox，则继续测空指令
        pass

    # 装配 spine 的 inbox 到全局，测 400
    kernel = get_kernel()
    if kernel.identity_registry is None:
        kernel.identity_registry = spine["registry"]
    wf_mod._inbox_singleton = spine["inbox"]  # type: ignore[attr-defined]
    try:
        r_empty = client.post(
            "/api/kernel/inbox",
            json={"identity_id": "", "instruction": "做点事"},
        )
        if r_empty.status_code in (401, 403):
            pytest.skip("auth required")
        assert r_empty.status_code == 400
        assert "员工" in r_empty.json()["detail"] or "identity" in r_empty.json()["detail"].lower()

        r_no_inst = client.post(
            "/api/kernel/inbox",
            json={"identity_id": str(uuid.uuid4()), "instruction": "  "},
        )
        assert r_no_inst.status_code == 400
        assert "指令" in r_no_inst.json()["detail"]
    finally:
        wf_mod.reset_workforce_for_tests()
        reset_kernel_for_tests()


def test_http_hire_and_enqueue_happy_path(spine) -> None:
    """HTTP：create identity (skill pack) → enqueue → 200 + message。"""
    from fastapi.testclient import TestClient

    from backend.kernel import get_kernel, reset_kernel_for_tests
    from backend.kernel import workforce as wf_mod
    from backend.main import app

    reset_kernel_for_tests()
    wf_mod.reset_workforce_for_tests()
    kernel = get_kernel()
    # 用测试 registry/session，避免污染全局 DB 时 identity 查不到
    kernel.identity_registry = spine["registry"]
    wf_mod._inbox_singleton = spine["inbox"]  # type: ignore[attr-defined]

    client = TestClient(app)
    try:
        # 直接经 registry 招人（HTTP create 可能写全局 DB）；再 HTTP 派活
        ident = _run(
            spine["registry"].create(
                f"HTTP员工{uuid.uuid4().hex[:6]}",
                role="测试",
                capabilities=["file_read"],
                default_token_budget=1000,
                meta={"source": "hire_wizard"},
            )
        )
        r = client.post(
            "/api/kernel/inbox",
            json={
                "identity_id": str(ident.id),
                "instruction": "烟雾：列出今日三件要事",
                "source": "manual",
                "priority": 1,
            },
        )
        if r.status_code in (401, 403):
            pytest.skip("auth required")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "pending"
        assert body.get("identity_name") == ident.name
        assert "message" in body and ident.name in body["message"]

        # mock 执行闭环
        async def mock_executor(identity, it, proc_id, k):
            return "done-by-mock"

        disp = WorkforceDispatcher(
            kernel,
            spine["inbox"],
            spine["registry"],
            spine["SessionLocal"],
            executor=mock_executor,
        )
        n = _run(disp.tick(wait=True))
        assert n == 1
        done = _run(spine["inbox"].list_items(status="done"))
        assert len(done) >= 1
    finally:
        wf_mod.reset_workforce_for_tests()
        reset_kernel_for_tests()
