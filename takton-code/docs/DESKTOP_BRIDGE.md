# Desktop Bridge Contract (Takton Code ↔ Takton Desktop)

Takton Code is an **independent** process. Desktop integration is optional and
goes through a stable HTTP JSON API under `/bridge/v1/*`.

## Enable from Code

```toml
# ~/.takton-code/config.toml
[bridge]
enabled = true
base_url = "http://127.0.0.1:8090/api"
api_token = ""  # or TAKTON_CODE_BRIDGE_TOKEN
use_desktop_models = true
use_desktop_skills = true
use_desktop_tools = true
use_desktop_mcp = true
use_desktop_rag = true
```

Env:

- `TAKTON_CODE_BRIDGE_ENABLED=true`
- `TAKTON_CODE_BRIDGE_URL=http://127.0.0.1:8090/api`
- `TAKTON_CODE_BRIDGE_TOKEN=...`

## Routes Desktop must implement

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/bridge/v1/health` | `{ok, version, capabilities[]}` |
| GET | `/bridge/v1/models` | list models (OpenAI-ish or `{data:[ModelInfo]}`) |
| POST | `/bridge/v1/chat/completions` | chat with optional tools; honor session snapshot if `session_id` |
| GET | `/bridge/v1/skills` | `SkillInfo[]` incl. optional `prompt_injection` |
| GET | `/bridge/v1/tools` | unified tool catalog |
| POST | `/bridge/v1/tools/invoke` | `{name, arguments, session_id?, project_root?}` → `{ok, output, error?}` |
| GET | `/bridge/v1/mcp` | MCP server list |
| POST | `/bridge/v1/rag/search` | `{query, top_k}` → hits |
| GET | `/bridge/v1/settings` | optional mirrored settings |

Schemas: `takton_code.bridge.protocol`.

## Code-side consumers (already wired)

| Feature | Code path | Behavior when bridge off |
|---------|-----------|--------------------------|
| Models | `build_llm_provider(use_bridge=True)` | local OpenAI-compatible |
| Skills inject | `AgentRuntime.setup` list_skills | skip |
| Tools | `desktop_invoke_tool` / `list_desktop_skills` | error string, agent continues |
| RAG | `desktop_rag_search` | error string |
| MCP | via `desktop_invoke_tool` names from Desktop | n/a |

## Desktop implementation sketch (FastAPI)

```python
router = APIRouter(prefix="/bridge/v1")

@router.get("/health")
async def health():
    return {"ok": True, "version": "0.2.0", "capabilities": ["models","skills","tools","mcp","rag"]}

@router.get("/models")
async def models(...):
    ...

@router.post("/chat/completions")
async def chat(body: dict, ...):
    # reuse existing LLMServiceFactory / session snapshot
    ...

@router.post("/tools/invoke")
async def invoke(...):
    # dispatch to ToolRegistry / MCP hub
    ...

@router.post("/rag/search")
async def rag(...):
    # existing knowledge search
    ...
```

## Non-goals

- Code does **not** import Desktop Python packages.
- Desktop does **not** need to embed the Code TUI.
- Auth: Bearer token recommended; local single-user may allow loopback without token.
