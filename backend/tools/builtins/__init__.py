"""Built-in tools package — core executors + self-config + workflow."""

from __future__ import annotations

from backend.tools.builtins.core_tools import BUILTIN_TOOL_CLASSES
from backend.tools.builtins.self_config import (
    GetSystemStatus,
    ListAvailableModels,
    ManageCron,
    ManageKnowledge,
    UpdateConfig,
)
from backend.tools.builtins.workflow_tools import (
    GenerateWorkflow,
    SaveWorkflow,
    UpdateDag,
    ValidateDag,
)

# Prefer package self-config list; core_tools also defines a fallback
try:
    from backend.tools.builtins.core_tools import SELF_CONFIG_TOOLS as _CORE_SELF
except ImportError:
    _CORE_SELF = []

SELF_CONFIG_TOOLS = [
    GetSystemStatus,
    UpdateConfig,
    ListAvailableModels,
    ManageKnowledge,
    ManageCron,
] or _CORE_SELF

__all__ = [
    "BUILTIN_TOOL_CLASSES",
    "SELF_CONFIG_TOOLS",
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
