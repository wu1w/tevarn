"""ToolExecutorPort over Unified ToolRegistry."""
from __future__ import annotations

from typing import Any


class RegistryToolExecutor:
    def __init__(self, registry: Any | None = None) -> None:
        if registry is None:
            from backend.tools.registry import ToolRegistry

            registry = ToolRegistry
        self._reg = registry

    async def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        return await self._reg.execute(name, arguments or {})

    def list_schemas(self, names: set[str] | None = None) -> list[dict[str, Any]]:
        return self._reg.get_tools_schema(names)
