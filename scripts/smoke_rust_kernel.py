#!/usr/bin/env python3
"""Smoke test: Rust kernel host + Python adapter.

Usage (repo root)::

    cargo build -p takton-kernel-host
    python scripts/smoke_rust_kernel.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TAKTON_KERNEL_BACKEND", "rust")
os.environ.setdefault("TAKTON_KERNEL_AUTO_START", "1")


def main() -> int:
    from backend.kernel_rust import (
        is_rust_host_available,
        start_kernel_host,
        RustAgentKernel,
        KernelPermissionError,
        BudgetExceededError,
    )

    print("host available?", is_rust_host_available())
    if not is_rust_host_available():
        ok = start_kernel_host()
        print("start_kernel_host ->", ok)
        if not ok:
            print("FAIL: cannot start host (build cargo first)")
            return 1

    k = RustAgentKernel(auto_start=False)
    health = k.health()
    print("health:", health)

    async def run() -> None:
        p = await k.create_process("smoke", session_id="s1", capabilities=["file_read", "grep"], token_budget=1000)
        print("process:", p.id, p.state, p.capabilities)
        await k.mark_running(p.id)
        d = await k.mediate(p.id, "tool_call", "file_read")
        print("mediate allow:", d)
        try:
            await k.mediate(p.id, "tool_call", "terminal")
            print("FAIL: terminal should deny")
            raise SystemExit(2)
        except KernelPermissionError as e:
            print("mediate deny ok:", e)
        rem = k.charge_tokens(p.id, 100)
        print("charge remaining:", rem)
        usage = k.resource_usage(p.id)
        print("resources:", usage)
        ok, idx = k.verify_event_chain()
        print("chain:", ok, idx)
        assert ok
        await k.end_process(p.id, state="completed", reason="smoke")
        print("OK smoke_rust_kernel")

    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
