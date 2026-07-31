"""H3: ABI fail-closed + runtime health shape."""

from __future__ import annotations

import pytest

from backend.kernel_rust.abi_gate import (
    REQUIRED_ABI_METHODS,
    AbiMismatchError,
    assert_required_abi,
    check_required_abi,
)


def test_required_abi_methods_nonempty() -> None:
    assert len(REQUIRED_ABI_METHODS) >= 15
    assert "mediate" in REQUIRED_ABI_METHODS
    assert "decide_tool" in REQUIRED_ABI_METHODS


def test_assert_required_abi_raises() -> None:
    with pytest.raises(AbiMismatchError) as ei:
        assert_required_abi(["ping", "health"])
    assert "mediate" in ei.value.missing


def test_check_required_abi_ok_when_complete() -> None:
    r = check_required_abi(set(REQUIRED_ABI_METHODS) | {"extra_method"})
    assert r["ok"] is True
    assert r["missing"] == []


def test_runtime_health_shape() -> None:
    from backend.services.runtime_health import collect_runtime_health

    h = collect_runtime_health()
    assert "ok" in h
    assert "severity" in h
    assert "host" in h
    assert "issues" in h
    assert "actions" in h
    assert "scenario" in h
