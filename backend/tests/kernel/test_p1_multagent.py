"""P1: IPC · inbox · skill-gate · services · context · memory."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("TEVARN_KERNEL_BACKEND", "rust")
os.environ.setdefault("TEVARN_KERNEL_AUTO_START", "1")


@pytest.fixture(scope="module")
def k():
    from backend.kernel_rust.client import (
        RustAgentKernel,
        is_rust_host_available,
        reset_rust_kernel_for_tests,
        start_kernel_host,
    )

    reset_rust_kernel_for_tests()
    kernel = None
    for _ in range(8):
        try:
            if not is_rust_host_available():
                start_kernel_host()
            kernel = RustAgentKernel(auto_start=True)
            break
        except Exception:
            time.sleep(0.2)
    if kernel is None:
        pytest.skip("host unavailable")
    yield kernel
    try:
        kernel._rpc.close()
    except Exception:
        pass
    reset_rust_kernel_for_tests()


@pytest.mark.asyncio
async def test_ipc_auth(k) -> None:
    a = await k.create_process(
        "p1_a",
        capabilities=["ipc_send", "ipc_recv", "ipc"],
        intent={
            "goal": "c",
            "capabilities": ["ipc_send", "ipc_recv", "ipc"],
            "constraints": {},
        },
    )
    b = await k.create_process(
        "p1_b",
        capabilities=["ipc_send", "ipc_recv", "ipc"],
        intent={
            "goal": "c",
            "capabilities": ["ipc_send", "ipc_recv", "ipc"],
            "constraints": {},
        },
    )
    m = k.ipc_send(a.id, b.id, "ping", {"n": 1})
    assert m.get("id")
    r = k.ipc_recv(b.id)
    assert r.get("count") == 1
    await k.end_process(a.id, state="completed")
    await k.end_process(b.id, state="completed")


@pytest.mark.asyncio
async def test_inbox_no_double_claim(k) -> None:
    k.inbox_submit("worker_id", "job", priority=1)
    c1 = k.inbox_claim("w1", "worker_id")
    c2 = k.inbox_claim("w2", "worker_id")
    assert c1.get("claimed") is True
    assert c2.get("claimed") is False


@pytest.mark.asyncio
async def test_skill_gate_and_evolution_policy(k) -> None:
    pkg = k.skill_register("p1_skill", "x=1", tests=["t1"])
    with pytest.raises(Exception):
        k.skill_activate(pkg["id"])
    k.skill_verify(pkg["id"])
    k.skill_activate(pkg["id"])
    assert k.skill_is_loadable("p1_skill")
    pol = k.evolution_policy()
    assert pol.get("auto_apply") is False
    assert pol.get("auto_apply_live_caps") is False


@pytest.mark.asyncio
async def test_services_and_memory_layers(k) -> None:
    k.sys_memory_put("u1", "k", 42)
    assert k.sys_memory_get("u1", "k").get("found") is True
    k.memory_layer_put("u1", "working", "fact", 0.95)
    r = k.memory_layer_consolidate("u1")
    assert int(r.get("promoted_to_episodic") or 0) >= 1


def test_evolution_config_default_no_auto_apply() -> None:
    from backend.evolution.config import get_evolution_config, set_evolution_config

    cfg = get_evolution_config()
    assert cfg.auto_apply_skills is False
    # cannot force true (P1-B G4 hard redline)
    cfg2 = set_evolution_config(auto_apply_skills=True)
    assert cfg2.auto_apply_skills is False
