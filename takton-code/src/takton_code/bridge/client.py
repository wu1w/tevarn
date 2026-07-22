"""HTTP client for Takton Desktop bridge (no-op when disabled)."""

from __future__ import annotations

from typing import Any

import httpx

from takton_code.bridge.protocol import (
    BridgeConfig,
    ChatRequest,
    EvolutionAssetInfo,
    EvolutionOutcomeRequest,
    MCPServerInfo,
    ModelInfo,
    RAGHit,
    RAGQuery,
    SkillInfo,
    ToolInfo,
    ToolInvokeRequest,
    ToolInvokeResult,
)


class BridgeError(RuntimeError):
    pass


class NullBridge:
    """Standalone mode — all desktop features unavailable."""

    enabled = False

    async def health(self) -> dict[str, Any]:
        return {"ok": False, "enabled": False, "reason": "bridge_disabled"}

    async def list_models(self) -> list[ModelInfo]:
        return []

    async def chat(self, req: ChatRequest) -> dict[str, Any]:
        raise BridgeError("bridge disabled")

    async def list_skills(self) -> list[SkillInfo]:
        return []

    async def list_tools(self) -> list[ToolInfo]:
        return []

    async def invoke_tool(self, req: ToolInvokeRequest) -> ToolInvokeResult:
        return ToolInvokeResult(ok=False, output="", error="bridge disabled")

    async def list_mcp(self) -> list[MCPServerInfo]:
        return []

    async def rag_search(self, req: RAGQuery) -> list[RAGHit]:
        return []

    async def evolution_status(self) -> dict[str, Any]:
        return {"enabled": False, "reason": "bridge_disabled"}

    async def evolution_assets(self) -> list[EvolutionAssetInfo]:
        return []

    async def report_outcome(self, req: EvolutionOutcomeRequest) -> dict[str, Any]:
        return {"ok": False, "reason": "bridge_disabled"}

    async def close(self) -> None:
        return None


class TaktonBridge:
    """HTTP bridge to Takton Desktop backend.

    Desktop side should expose /bridge/v1/* as documented in protocol.BRIDGE_ROUTES.
    Until Desktop ships those routes, Code stays fully independent.
    """

    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self.enabled = bool(config.enabled)
        self._client: httpx.AsyncClient | None = None

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.config.api_token:
            h["Authorization"] = f"Bearer {self.config.api_token}"
        return h

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            base = self.config.base_url.rstrip("/")
            self._client = httpx.AsyncClient(
                base_url=base,
                headers=self._headers(),
                timeout=self.config.timeout_sec,
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def health(self) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "enabled": False}
        try:
            c = await self._get_client()
            r = await c.get("/bridge/v1/health")
            if r.status_code >= 400:
                return {"ok": False, "enabled": True, "status": r.status_code, "body": r.text[:300]}
            return {"ok": True, "enabled": True, **(r.json() if r.content else {})}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "enabled": True, "error": str(e)}

    async def list_models(self) -> list[ModelInfo]:
        c = await self._get_client()
        r = await c.get("/bridge/v1/models")
        r.raise_for_status()
        data = r.json()
        items = data if isinstance(data, list) else data.get("data") or data.get("models") or []
        return [ModelInfo.model_validate(x) for x in items]

    async def chat(self, req: ChatRequest) -> dict[str, Any]:
        c = await self._get_client()
        r = await c.post("/bridge/v1/chat/completions", json=req.model_dump(exclude_none=True))
        r.raise_for_status()
        return r.json()

    async def list_skills(self) -> list[SkillInfo]:
        c = await self._get_client()
        r = await c.get("/bridge/v1/skills")
        r.raise_for_status()
        data = r.json()
        items = data if isinstance(data, list) else data.get("skills") or data.get("data") or []
        return [SkillInfo.model_validate(x) for x in items]

    async def list_tools(self) -> list[ToolInfo]:
        c = await self._get_client()
        r = await c.get("/bridge/v1/tools")
        r.raise_for_status()
        data = r.json()
        items = data if isinstance(data, list) else data.get("tools") or data.get("data") or []
        return [ToolInfo.model_validate(x) for x in items]

    async def invoke_tool(self, req: ToolInvokeRequest) -> ToolInvokeResult:
        c = await self._get_client()
        r = await c.post("/bridge/v1/tools/invoke", json=req.model_dump(exclude_none=True))
        if r.status_code >= 400:
            return ToolInvokeResult(ok=False, output="", error=f"HTTP {r.status_code}: {r.text[:500]}")
        return ToolInvokeResult.model_validate(r.json())

    async def list_mcp(self) -> list[MCPServerInfo]:
        c = await self._get_client()
        r = await c.get("/bridge/v1/mcp")
        r.raise_for_status()
        data = r.json()
        items = data if isinstance(data, list) else data.get("servers") or data.get("data") or []
        return [MCPServerInfo.model_validate(x) for x in items]

    async def rag_search(self, req: RAGQuery) -> list[RAGHit]:
        c = await self._get_client()
        r = await c.post("/bridge/v1/rag/search", json=req.model_dump(exclude_none=True))
        r.raise_for_status()
        data = r.json()
        items = data if isinstance(data, list) else data.get("hits") or data.get("results") or []
        return [RAGHit.model_validate(x) for x in items]

    async def evolution_status(self) -> dict[str, Any]:
        c = await self._get_client()
        r = await c.get("/bridge/v1/evolution/status")
        r.raise_for_status()
        return r.json()

    async def evolution_assets(self) -> list[EvolutionAssetInfo]:
        c = await self._get_client()
        r = await c.get("/bridge/v1/evolution/assets")
        r.raise_for_status()
        data = r.json()
        items = data if isinstance(data, list) else data.get("assets") or data.get("data") or []
        return [EvolutionAssetInfo.model_validate(x) for x in items]

    async def report_outcome(self, req: EvolutionOutcomeRequest) -> dict[str, Any]:
        c = await self._get_client()
        r = await c.post("/bridge/v1/evolution/from_task", json=req.model_dump(exclude_none=True))
        r.raise_for_status()
        return r.json()


def build_bridge(config: BridgeConfig) -> TaktonBridge | NullBridge:
    if config.enabled:
        return TaktonBridge(config)
    return NullBridge()
