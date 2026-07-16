"""Package + system layers smoke tests (static + light async)."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_package_manifest_files_exist():
    assert (ROOT / "backend/packages/manifest.py").is_file()
    assert (ROOT / "backend/packages/loader.py").is_file()
    assert (ROOT / "backend/packages/session_packages.py").is_file()
    assert (ROOT / "backend/api/routes/packages.py").is_file()
    assert (ROOT / "backend/agent/system_layers.py").is_file()


def test_workspace_example_package():
    p = ROOT / "workspace/packages/code-review-lite/takton.package.json"
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    assert "code-review-lite" in text
    assert "system_snippet" in text


def test_routes_registered():
    init = (ROOT / "backend/api/routes/__init__.py").read_text(encoding="utf-8")
    assert "packages" in init
    assert "packages.router" in init
    ctx = (ROOT / "backend/api/routes/context.py").read_text(encoding="utf-8")
    assert "system-layers" in ctx
    assert "get_system_layers" in ctx


def test_context_injects_packages():
    src = (ROOT / "backend/agent/context.py").read_text(encoding="utf-8")
    assert "resolve_attached_snippets" in src
    assert "Attached Takton Packages" in src
    assert "last_system_layers" in src
    compile(src, "context.py", "exec")


def test_system_layers_module_compiles():
    src = (ROOT / "backend/agent/system_layers.py").read_text(encoding="utf-8")
    compile(src, "system_layers.py", "exec")
    assert "build_system_layers_report" in src


def test_frontend_panel_and_api():
    panel = ROOT / "frontend/components/context/SystemLayersPanel.tsx"
    assert panel.is_file()
    text = panel.read_text(encoding="utf-8")
    assert "System 分层" in text
    assert "Takton Packages" in text
    api = (ROOT / "frontend/lib/api.ts").read_text(encoding="utf-8")
    assert "getSystemLayers" in api
    assert "listPackages" in api
    assert "attachPackage" in api
    page = (ROOT / "frontend/app/context/page.tsx").read_text(encoding="utf-8")
    assert "SystemLayersPanel" in page


def test_loader_finds_workspace_package():
    from backend.packages.loader import load_workspace_packages

    pkgs = load_workspace_packages()
    names = {p.name for p in pkgs}
    assert "code-review-lite" in names
