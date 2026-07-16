"""Register evolution playbooks as executable ToolRegistry tools."""

from __future__ import annotations

import logging
from typing import Any

from backend.tools.base import BaseTool, ToolRiskLevel, ToolSource
from backend.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class EvolvedPlaybookTool(BaseTool):
    """Runtime tool backed by an evolution asset (playbook body)."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        body: str,
        asset_id: str | None = None,
        enabled: bool = True,
    ):
        super().__init__(
            name=name,
            description=description or f"进化 playbook: {name}",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "用户问题或需要套用该 playbook 的上下文",
                    },
                    "context": {
                        "type": "string",
                        "description": "可选补充上下文",
                    },
                },
            },
            source=ToolSource.DYNAMIC,
            risk_level=ToolRiskLevel.LOW,
            enabled=enabled,
        )
        self.body = body or ""
        self.asset_id = asset_id

    async def execute(self, **kwargs: Any) -> Any:
        query = str(kwargs.get("query") or kwargs.get("context") or "").strip()
        # Bump use count when actually invoked
        try:
            from backend.evolution import store

            store.bump_use(self.name, kind="skill")
        except Exception:
            pass

        guide = self.body.strip()
        if not guide:
            return f"[evolution:{self.name}] playbook 为空"
        header = f"[evolution playbook: {self.name}]\n"
        if query:
            header += f"用户上下文: {query}\n---\n"
        # Return playbook so the agent can follow it in the next reasoning step
        out = header + guide
        if len(out) > 12000:
            out = out[:12000] + "\n…[truncated]"
        return out


def register_evolved_tool(
    *,
    name: str,
    description: str,
    body: str,
    asset_id: str | None = None,
    enabled: bool = True,
) -> None:
    if not name:
        return
    if not enabled:
        ToolRegistry.unregister(name)
        return
    tool = EvolvedPlaybookTool(
        name=name,
        description=description,
        body=body,
        asset_id=asset_id,
        enabled=True,
    )
    ToolRegistry.register(tool)
    logger.info("Registered evolution tool: %s", name)


def unregister_evolved_tool(name: str) -> None:
    ToolRegistry.unregister(name)


def load_active_evolution_tools() -> int:
    """Load active evolution skill assets into ToolRegistry. Returns count."""
    try:
        from backend.evolution import store
        from backend.evolution.manager import get_evolution_manager

        get_evolution_manager().ensure_seeded()
        assets = store.list_assets(kind="skill", status="active", limit=500)
        n = 0
        for a in assets:
            if a.get("source") == "seed":
                continue
            register_evolved_tool(
                name=a["name"],
                description=a.get("summary") or a["name"],
                body=a.get("content") or "",
                asset_id=a.get("id"),
                enabled=True,
            )
            n += 1
        logger.info("Loaded %s active evolution tools", n)
        return n
    except Exception as e:
        logger.warning("load_active_evolution_tools failed: %s", e)
        return 0
