"""Smoke tests for sub-agent workflow canvas integration."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_sub_agent_node_type_defined():
    src = _read("backend/schemas/workflow_node.py")
    tree = ast.parse(src)
    assert "sub_agent" in src
    # compile ok
    compile(src, "workflow_node.py", "exec")


def test_workflow_engine_has_sub_agent_handler():
    src = _read("backend/services/workflow_engine.py")
    assert '"sub_agent": self._exec_sub_agent' in src or "'sub_agent': self._exec_sub_agent" in src
    assert "async def _exec_sub_agent" in src
    compile(src, "workflow_engine.py", "exec")


def test_generate_workflow_tool_mentions_sub_agent():
    src = _read("backend/tools/builtins/workflow_tools.py")
    compile(src, "workflow_tools.py", "exec")
    assert "sub_agent" in src
    assert "generate_workflow" in src
    assert "save_workflow" in src


def test_workflow_tools_registered_in_loader():
    src = _read("backend/tools/loader.py")
    assert "GenerateWorkflow" in src
    assert "workflow tools" in src.lower() or "workflow_tool_classes" in src


def test_nl_api_route_exists():
    src = _read("backend/api/routes/workflows.py")
    assert "generate-from-nl" in src
    assert "generate_workflow_from_nl" in src


def test_frontend_palette_has_subagent_cards():
    src = _read("frontend/components/workflow/NodePalette.tsx")
    assert "subAgent" in src or "sub_agent" in src
    assert "subAgentApi" in src


def test_frontend_canvas_accepts_sub_agent_payload():
    src = _read("frontend/components/workflow/WorkflowCanvas.tsx")
    assert "kind === 'sub_agent'" in src or 'kind === "sub_agent"' in src
    assert "sub_agent_id" in src


def test_workflows_page_nl_bar():
    src = _read("frontend/app/workflows/page.tsx")
    assert "generateWorkflowFromNl" in src
    assert "handleNlGenerate" in src
    assert "AI 生成" in src


def test_unpack_backend_synced():
    base = ROOT / "frontend/release/win-unpacked/resources/backend"
    assert (base / "schemas/workflow_node.py").exists()
    text = (base / "schemas/workflow_node.py").read_text(encoding="utf-8")
    assert "sub_agent" in text
    loader = (base / "tools/loader.py").read_text(encoding="utf-8")
    assert "GenerateWorkflow" in loader
