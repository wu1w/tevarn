from __future__ import annotations

import pytest

from backend.schemas.workflow_node import get_all_node_type_definitions
from backend.services.workflow_engine import (
    WorkflowContext,
    WorkflowEngine,
    WorkflowExecutionError,
)


def test_unimplemented_loop_is_not_advertised_to_editor() -> None:
    assert "loop" not in {node.type for node in get_all_node_type_definitions()}


@pytest.mark.asyncio
async def test_legacy_loop_fails_instead_of_processing_only_first_item() -> None:
    engine = WorkflowEngine()
    with pytest.raises(WorkflowExecutionError, match="循环节点尚未实现"):
        await engine._exec_loop(
            {"item_variable": "item"},
            {"items": ["first", "second"]},
            WorkflowContext(),
        )
