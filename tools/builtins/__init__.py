"""backend/tools/builtins/__init__.py"""

from backend.tools.builtins.self_config import (
    GetSystemStatus,
    ListAvailableModels,
    ManageCron,
    ManageKnowledge,
    UpdateConfig,
)

from backend.tools.builtins.workflow_tools import (
    GenerateWorkflow,
    UpdateDag,
    ValidateDag,
    SaveWorkflow,
)

__all__ = [
    "GetSystemStatus",
    "UpdateConfig",
    "ListAvailableModels",
    "ManageKnowledge",
    "ManageCron",
    "GenerateWorkflow",
    "UpdateDag",
    "ValidateDag",
    "SaveWorkflow",
]

# 导出 BUILTIN_TOOL_CLASSES 供 loader.py 使用
BUILTIN_TOOL_CLASSES: list = []
