"""Agent 资源管理工具集 — Phase 2.4 按域拆分后的兼容 re-export。

域模块：
- manage_crew_tools: ManageSubAgent / ManageSkill / ManageProfile
- manage_integration_tools: ManageMcp / ManageChannel / ManageWebhook
- manage_ops_tools: QueryAuditLog / ListTasks / ManagePackage / ManageGit / QueryEvolution / ManageEvolution
- manage_common: 共享辅助
"""
from __future__ import annotations

from backend.tools.builtins.manage_crew_tools import (
    ManageProfile,
    ManageSkill,
    ManageSubAgent,
)
from backend.tools.builtins.manage_integration_tools import (
    ManageChannel,
    ManageMcp,
    ManageWebhook,
)
from backend.tools.builtins.manage_ops_tools import (
    ListTasks,
    ManageEvolution,
    ManageGit,
    ManagePackage,
    QueryAuditLog,
    QueryEvolution,
)

__all__ = [
    "ListTasks",
    "ManageChannel",
    "ManageEvolution",
    "ManageGit",
    "ManageMcp",
    "ManagePackage",
    "ManageProfile",
    "ManageSkill",
    "ManageSubAgent",
    "ManageWebhook",
    "QueryAuditLog",
    "QueryEvolution",
]
