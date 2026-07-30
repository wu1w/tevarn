"""
Tool 服务模块

Agent 可调用的工具注册表与执行器。
v3.0 新增：统一工具注册表代理（兼容旧入口）
"""

from backend.tools.registry import ToolRegistry as UnifiedToolRegistry

from .executors import EXECUTOR_MAP
from .registry import ToolRegistry

__all__ = ["ToolRegistry", "UnifiedToolRegistry", "EXECUTOR_MAP"]
