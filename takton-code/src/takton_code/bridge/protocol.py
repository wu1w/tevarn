"""Desktop bridge protocol — reserved integration surface for Takton Desktop.

Takton Code runs independently. When bridge.enabled=true, the agent may
delegate models / skills / tools / MCP / RAG to a running Takton Desktop backend
without rewriting the agent core.

Wire format is stable JSON over HTTP. Desktop implementers should match these
paths and schemas.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class BridgeCapability(str, Enum):
    MODELS = "models"
    SKILLS = "skills"
    TOOLS = "tools"
    MCP = "mcp"
    RAG = "rag"
    SESSIONS = "sessions"
    SETTINGS = "settings"


class BridgeConfig(BaseModel):
    enabled: bool = False
    base_url: str = "http://127.0.0.1:8090/api"
    api_token: str = ""
    timeout_sec: float = 60.0
    capabilities: list[BridgeCapability] = Field(
        default_factory=lambda: list(BridgeCapability)
    )


class ModelInfo(BaseModel):
    id: str
    name: str | None = None
    provider: str | None = None
    context_window: int | None = None
    description: str | None = None


class SkillInfo(BaseModel):
    name: str
    description: str = ""
    enabled: bool = True
    source: Literal["builtin", "store", "evolution", "project"] = "builtin"
    prompt_injection: str | None = None


class ToolInfo(BaseModel):
    name: str
    description: str = ""
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    risk_level: Literal["low", "medium", "high"] = "low"
    source: Literal["builtin", "mcp", "desktop", "custom"] = "builtin"


class MCPServerInfo(BaseModel):
    name: str
    status: str = "unknown"
    tools: list[str] = Field(default_factory=list)


class RAGQuery(BaseModel):
    query: str
    top_k: int = 5
    collection: str | None = None


class RAGHit(BaseModel):
    content: str
    score: float | None = None
    source: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    role: str
    content: str | None = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    tools: list[dict[str, Any]] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    # session snapshot lock (desktop should honor)
    session_id: str | None = None


class ChatChoiceDelta(BaseModel):
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    finish_reason: str | None = None


class ToolInvokeRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None
    project_root: str | None = None


class ToolInvokeResult(BaseModel):
    ok: bool
    output: str
    error: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


# --- Evolution（Code → Desktop 回流 / 读引擎状态）---


class EvolutionAssetInfo(BaseModel):
    id: str | None = None
    kind: str | None = None
    name: str
    summary: str = ""
    source: str | None = None
    status: str | None = None
    use_count: int = 0


class EvolutionOutcomeRequest(BaseModel):
    """Code 侧把一次 task 的结果/学到的模式喂回 Desktop evolution 管线。"""

    task_name: str
    success: bool = True
    detail: str = ""
    failure_codes: list[str] = Field(default_factory=list)
    source: str = "takton-code"


# --- HTTP path contract (Desktop must implement when bridge is on) ---

BRIDGE_ROUTES = {
    "health": "GET /bridge/v1/health",
    "list_models": "GET /bridge/v1/models",
    "chat": "POST /bridge/v1/chat/completions",
    "list_skills": "GET /bridge/v1/skills",
    "list_tools": "GET /bridge/v1/tools",
    "invoke_tool": "POST /bridge/v1/tools/invoke",
    "list_mcp": "GET /bridge/v1/mcp",
    "rag_search": "POST /bridge/v1/rag/search",
    "get_settings": "GET /bridge/v1/settings",
    "evolution_status": "GET /bridge/v1/evolution/status",
    "evolution_assets": "GET /bridge/v1/evolution/assets",
    "evolution_report": "POST /bridge/v1/evolution/from_task",
}


@runtime_checkable
class BridgeClientProtocol(Protocol):
    async def health(self) -> dict[str, Any]: ...
    async def list_models(self) -> list[ModelInfo]: ...
    async def chat(self, req: ChatRequest) -> dict[str, Any]: ...
    async def list_skills(self) -> list[SkillInfo]: ...
    async def list_tools(self) -> list[ToolInfo]: ...
    async def invoke_tool(self, req: ToolInvokeRequest) -> ToolInvokeResult: ...
    async def list_mcp(self) -> list[MCPServerInfo]: ...
    async def rag_search(self, req: RAGQuery) -> list[RAGHit]: ...
    async def evolution_status(self) -> dict[str, Any]: ...
    async def evolution_assets(self) -> list[EvolutionAssetInfo]: ...
    async def report_outcome(self, req: EvolutionOutcomeRequest) -> dict[str, Any]: ...
    async def close(self) -> None: ...
