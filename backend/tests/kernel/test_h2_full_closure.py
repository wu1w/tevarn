"""H2 full closure: has_capability fail-closed, process tree, governance status."""

from __future__ import annotations

import pytest


def test_has_capability_none_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEVARN_FORCE_PRODUCTION_GUARD", "1")
    monkeypatch.delenv("TEVARN_DEV_UNSAFE", raising=False)

    from backend.kernel.process import AgentProcess

    p = AgentProcess(identity="t", capabilities=None)
    assert p.has_capability("file_read") is False


def test_has_capability_none_open_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEVARN_DEV_UNSAFE", "1")
    monkeypatch.delenv("TEVARN_FORCE_PRODUCTION_GUARD", raising=False)

    from backend.kernel.process import AgentProcess

    p = AgentProcess(identity="t", capabilities=None)
    assert p.has_capability("file_read") is True


def test_from_dict_safe_rejects_forged() -> None:
    from backend.kernel.capability import CapabilityToken

    forged = {
        "capabilities": ["*"],
        "process_id": "x",
        "signature": "deadbeef",
    }
    assert CapabilityToken.from_dict_safe(forged) is None


def test_process_tree_build() -> None:
    """Unit: tree grouping logic mirrors API."""
    flat = [
        {"id": "p1", "parent_id": None, "identity": "root", "capabilities": ["file_read"]},
        {"id": "p2", "parent_id": "p1", "identity": "child", "capabilities": ["file_read"]},
        {"id": "p3", "parent_id": "p1", "identity": "child2", "capabilities": None},
    ]
    by_id = {}
    for p in flat:
        p = dict(p)
        p["children"] = []
        by_id[p["id"]] = p
    roots = []
    for pid, p in by_id.items():
        parent = p.get("parent_id")
        if parent and parent in by_id:
            by_id[parent]["children"].append(p)
        else:
            roots.append(p)
    assert len(roots) == 1
    assert len(roots[0]["children"]) == 2
