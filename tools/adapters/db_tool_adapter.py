"""
数据库 Tool 适配器

将 backend.services.tools.registry 中定义的数据库 Tool 类型
包装成 BaseTool，接入统一工具注册表。

主要把现有 execute_* 函数映射到 BaseTool.execute
"""

from __future__ import annotations

from typing import Any

from backend.services.tools.executors import EXECUTOR_MAP
from backend.tools.base import BaseTool, ToolRiskLevel, ToolSource


class DbToolAdapter(BaseTool):
    """
    适配数据库中由用户配置的 Tool 记录。

    字段映射：
        name        -> tool name
        description -> description
        type        -> executor map key
        config      -> 执行器 config
        schema      -> 参数 schema
        enabled     -> 是否启用
        is_builtin  -> 是否内置
    """

    def __init__(self, db_tool: Any, config: dict[str, Any] | None = None):
        super().__init__(
            name=db_tool.name,
            description=db_tool.description or "",
            parameters=db_tool.tool_schema or {"type": "object", "properties": {}},
            source=ToolSource.BUILTIN if db_tool.is_builtin else ToolSource.DB,
            risk_level=ToolRiskLevel.map(db_tool.risk_level)
            if hasattr(db_tool, "risk_level") and db_tool.risk_level
            else ToolRiskLevel.MEDIUM,
            enabled=getattr(db_tool, "enabled", True),
            requires_confirmation=getattr(db_tool, "requires_confirmation", False),
            allowed_paths=getattr(db_tool, "allowed_paths", None),
        )
        self.db_tool = db_tool
        self._config = config or (db_tool.config or {})
        self._executor_name = db_tool.type

    async def execute(self, **kwargs: Any) -> Any:
        executor = EXECUTOR_MAP.get(self._executor_name)
        if executor is None:
            return f"[Error] No executor found for tool type '{self._executor_name}'"

        # 合并用户参数与工具默认配置，使用合并后的配置
        merged_config = {**self._config, **kwargs}
        return await executor(merged_config, kwargs)
