"""Host availability for kernel tests.

If TEVARN_KERNEL_HOST_BIN is set (kernel-ci), missing/dead host is a FAIL,
not a skip. Local dev without a binary still skips.
"""
from __future__ import annotations
import os

def require_host_or_skip() -> None:
    import pytest
    from backend.kernel_rust.client import (
        _find_host_bin,
        is_rust_host_available,
        start_kernel_host,
    )
    if is_rust_host_available():
        return
    bin_path = os.environ.get("TEVARN_KERNEL_HOST_BIN") or _find_host_bin()
    if bin_path:
        if start_kernel_host():
            return
        if os.environ.get("TEVARN_KERNEL_HOST_BIN"):
            pytest.fail(f"TEVARN_KERNEL_HOST_BIN set but host failed to start: {bin_path}")
        pytest.skip("tevarn-kernel-host present but failed to start")
    if os.environ.get("TEVARN_KERNEL_HOST_BIN"):
        pytest.fail("TEVARN_KERNEL_HOST_BIN set but binary not usable")
    pytest.skip("tevarn-kernel-host binary not found; run: cargo build -p tevarn-kernel-host")
