"""0.7 DoD: kernel/ must not import fastapi or backend.api.* at module load."""

from __future__ import annotations

import ast
from pathlib import Path

_KERNEL = Path(__file__).resolve().parents[2] / "kernel"
_FORBIDDEN_MODS = ("fastapi",)
_FORBIDDEN_PREFIXES = ("backend.api", "backend.main")


def _imports_of(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out.append(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.append(node.module)
    return out


def test_kernel_package_has_no_fastapi_or_api_imports() -> None:
    bad: list[str] = []
    for py in sorted(_KERNEL.glob("*.py")):
        if py.name.startswith("_"):
            continue
        for mod in _imports_of(py):
            top = mod.split(".")[0]
            if top in _FORBIDDEN_MODS or mod.startswith(_FORBIDDEN_PREFIXES):
                bad.append(f"{py.name}: import {mod}")
            # also catch from backend.api...
            if mod.startswith("backend.api") or mod == "fastapi":
                bad.append(f"{py.name}: import {mod}")
    assert bad == [], "kernel must not depend on FastAPI/adapters:\n" + "\n".join(bad)


def test_ports_ws_manager_registry() -> None:
    from backend.kernel.ports import get_ws_manager, set_ws_manager

    set_ws_manager(None)
    assert get_ws_manager() is None
    sentinel = object()
    set_ws_manager(sentinel)
    assert get_ws_manager() is sentinel
    set_ws_manager(None)


def test_protocol_version_at_least_02() -> None:
    from backend.kernel.protocol_spec import PROTOCOL_VERSION, protocol_manifest

    assert PROTOCOL_VERSION.startswith("0.2") or PROTOCOL_VERSION.startswith("1.")
    m = protocol_manifest()
    assert "domain_events" in m.get("interop", {})
    assert m.get("client_guide", {}).get("snapshot_then_events") is True
