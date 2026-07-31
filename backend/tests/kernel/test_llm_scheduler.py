"""LLM 公平调度：槽位、主人优先、公平、日配额、release。"""

from __future__ import annotations

import asyncio

import pytest

from backend.kernel.llm_scheduler import (
    LlmAdmissionController,
    LlmAdmissionRejected,
    LlmLeaseRequest,
    Priority,
    reset_llm_admission_for_tests,
)


@pytest.fixture()
def ctrl(monkeypatch):
    # 单测验证 Python 调度语义；避免 host 残留槽位导致 acquire/poll 挂起
    monkeypatch.setenv("TAKTON_LLM_ALLOW_PY_FALLBACK", "1")
    monkeypatch.setattr(
        "backend.kernel.llm_admission._rust_kernel", lambda: None
    )
    reset_llm_admission_for_tests()
    c = LlmAdmissionController()
    monkeypatch.setattr(
        "backend.core.config.settings.llm_max_in_flight", 2, raising=False
    )
    monkeypatch.setattr(
        "backend.core.config.settings.llm_max_in_flight_per_identity", 1, raising=False
    )
    monkeypatch.setattr(
        "backend.core.config.settings.llm_owner_reserve_slots", 1, raising=False
    )
    monkeypatch.setattr(
        "backend.core.config.settings.llm_queue_max", 16, raising=False
    )
    monkeypatch.setattr(
        "backend.core.config.settings.llm_fairness_wait_weight", 1.0, raising=False
    )
    monkeypatch.setattr(
        "backend.core.config.settings.llm_daily_token_budget_global", 0, raising=False
    )
    monkeypatch.setattr(
        "backend.core.config.settings.llm_daily_token_budget_per_identity",
        0,
        raising=False,
    )
    # force _cfg to read monkeypatched settings
    yield c
    c.reset_for_tests()


def test_concurrent_cap_queues(ctrl, monkeypatch):
    monkeypatch.setattr(
        "backend.core.config.settings.llm_max_in_flight", 1, raising=False
    )
    monkeypatch.setattr(
        "backend.core.config.settings.llm_owner_reserve_slots", 0, raising=False
    )

    async def go():
        l1 = await ctrl.acquire(
            LlmLeaseRequest(source="workforce", priority=Priority.WORKFORCE_NORMAL)
        )
        assert len(ctrl.status()["in_flight"]) == 1

        task = asyncio.create_task(
            ctrl.acquire(
                LlmLeaseRequest(
                    source="workforce",
                    priority=Priority.WORKFORCE_NORMAL,
                    identity_id="b",
                )
            )
        )
        await asyncio.sleep(0.05)
        st = ctrl.status()
        assert st["counts"]["queued"] >= 1
        await ctrl.release(l1)
        l2 = await asyncio.wait_for(task, timeout=2)
        assert l2 is not None
        await ctrl.release(l2)
        assert ctrl.status()["counts"]["in_flight"] == 0

    asyncio.run(go())


def test_owner_priority_over_workforce(ctrl, monkeypatch):
    monkeypatch.setattr(
        "backend.core.config.settings.llm_max_in_flight", 1, raising=False
    )
    monkeypatch.setattr(
        "backend.core.config.settings.llm_owner_reserve_slots", 0, raising=False
    )

    async def go():
        holder = await ctrl.acquire(
            LlmLeaseRequest(source="workforce", priority=Priority.WORKFORCE_NORMAL)
        )
        # 先入队后台
        wf_task = asyncio.create_task(
            ctrl.acquire(
                LlmLeaseRequest(
                    source="workforce",
                    priority=Priority.WORKFORCE_LOW,
                    identity_id="wf1",
                )
            )
        )
        await asyncio.sleep(0.03)
        # 再入队主人
        owner_task = asyncio.create_task(
            ctrl.acquire(
                LlmLeaseRequest(source="chat", priority=Priority.OWNER_CHAT)
            )
        )
        await asyncio.sleep(0.03)
        await ctrl.release(holder)
        # 主人应先拿到
        done, pending = await asyncio.wait(
            {wf_task, owner_task}, return_when=asyncio.FIRST_COMPLETED, timeout=2
        )
        assert owner_task in done
        owner_lease = owner_task.result()
        assert owner_lease.is_owner or owner_lease.source == "chat"
        await ctrl.release(owner_lease)
        wf_lease = await asyncio.wait_for(wf_task, timeout=2)
        await ctrl.release(wf_lease)

    asyncio.run(go())


def test_fairness_two_identities_alternate(ctrl, monkeypatch):
    monkeypatch.setattr(
        "backend.core.config.settings.llm_max_in_flight", 1, raising=False
    )
    monkeypatch.setattr(
        "backend.core.config.settings.llm_owner_reserve_slots", 0, raising=False
    )
    monkeypatch.setattr(
        "backend.core.config.settings.llm_max_in_flight_per_identity", 1, raising=False
    )

    async def go():
        order: list[str] = []
        holder = await ctrl.acquire(
            LlmLeaseRequest(
                source="workforce",
                priority=Priority.WORKFORCE_NORMAL,
                identity_id="seed",
            )
        )

        async def run_one(iid: str):
            lease = await ctrl.acquire(
                LlmLeaseRequest(
                    source="workforce",
                    priority=Priority.WORKFORCE_NORMAL,
                    identity_id=iid,
                )
            )
            order.append(iid)
            await asyncio.sleep(0.02)
            await ctrl.release(lease)

        t1 = asyncio.create_task(run_one("a"))
        t2 = asyncio.create_task(run_one("b"))
        await asyncio.sleep(0.05)
        await ctrl.release(holder)
        await asyncio.gather(t1, t2)
        # 两者都应完成（不饿死）
        assert set(order) == {"a", "b"}

    asyncio.run(go())


def test_daily_quota_rejects(ctrl, monkeypatch):
    monkeypatch.setattr(
        "backend.core.config.settings.llm_daily_token_budget_global", 100, raising=False
    )
    monkeypatch.setattr(
        "backend.core.config.settings.llm_max_in_flight", 4, raising=False
    )
    monkeypatch.setattr(
        "backend.core.config.settings.llm_owner_reserve_slots", 0, raising=False
    )

    async def go():
        ctrl.charge_quota(None, 100)
        with pytest.raises(LlmAdmissionRejected) as ei:
            await ctrl.acquire(
                LlmLeaseRequest(source="chat", priority=Priority.OWNER_CHAT)
            )
        assert "quota" in ei.value.code or "配额" in str(ei.value)

    asyncio.run(go())


def test_release_on_exception_path(ctrl, monkeypatch):
    monkeypatch.setattr(
        "backend.core.config.settings.llm_max_in_flight", 1, raising=False
    )
    monkeypatch.setattr(
        "backend.core.config.settings.llm_owner_reserve_slots", 0, raising=False
    )

    async def go():
        async with ctrl.lease_context(
            LlmLeaseRequest(source="chat", priority=Priority.OWNER_CHAT)
        ) as lease:
            assert lease is not None
            assert ctrl.status()["counts"]["in_flight"] == 1
            raise RuntimeError("boom")

    async def wrap():
        with pytest.raises(RuntimeError):
            await go()
        assert ctrl.status()["counts"]["in_flight"] == 0

    asyncio.run(wrap())


def test_status_shape(ctrl):
    st = ctrl.status()
    assert "in_flight" in st
    assert "queued" in st
    assert "config" in st
    assert "quota" in st
    assert "llm_max_in_flight" in st["config"]
