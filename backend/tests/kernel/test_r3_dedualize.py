"""R3 P0–P4 de-dualize: domain · approval · cache · inbox claim."""

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
os.environ.setdefault("TEVARN_LLM_ALLOW_PY_FALLBACK", "1")


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
    for _ in range(10):
        try:
            if not is_rust_host_available():
                start_kernel_host()
            kernel = RustAgentKernel(auto_start=True)
            break
        except Exception:
            time.sleep(0.2)
    if kernel is None:
        pytest.skip("host")
    yield kernel
    try:
        kernel._rpc.close()
    except Exception:
        pass
    reset_rust_kernel_for_tests()


def test_domain_and_approval(k) -> None:
    k._call("domain_publish", {"topic": "job.x", "payload": {"a": 1}})
    r = k._call("domain_recent", {"limit": 3, "prefix": "job."}) or {}
    assert "events" in r
    k._call(
        "approval_set_rules",
        {"rules": [{"key": "auto_low_risk", "enabled": True}]},
    )
    c = k._call("approval_classify", {"capabilities": ["file_read"]}) or {}
    assert c.get("kind") == "low"
    a = k._call("approval_should_auto", {"capabilities": ["file_read"]}) or {}
    assert a.get("auto_approve") is True
    h = k._call("approval_should_auto", {"capabilities": ["terminal"]}) or {}
    assert h.get("auto_approve") is False


def test_inbox_no_double_claim(k) -> None:
    k._call(
        "inbox_submit",
        {"identity": "w", "instruction": "t", "priority": 1, "meta": {}},
    )
    c1 = k._call("inbox_claim", {"worker_id": "a"})
    c2 = k._call("inbox_claim", {"worker_id": "b"})
    assert c1.get("claimed") is True
    assert c2.get("claimed") is False


def test_identity_cache_and_memory(k) -> None:
    k._call(
        "identity_cache_put",
        {
            "identity": {
                "id": "i1",
                "name": "Alice",
                "status": "active",
                "capabilities": ["file_read"],
            }
        },
    )
    g = k._call("identity_cache_get", {"id": "Alice"}) or {}
    assert g.get("id") == "i1"
    k._call(
        "sys_memory_put",
        {"identity": "i1", "key": "k", "value": 1},
    )
    m = k._call("sys_memory_get", {"identity": "i1", "key": "k"}) or {}
    assert m.get("found") is True


def test_py_approval_shim_uses_rust(k) -> None:
    from backend.kernel.approval_rules import classify_caps, evolution_requires_review

    assert classify_caps(["grep"]) == "low"
    assert classify_caps(["command"]) == "high"
    assert evolution_requires_review() is True
