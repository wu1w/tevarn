# Tevarn Mobile Agent Kernel 0.4

## Capabilities (Codex / 豆包-class subset on phone)

- **Tools**: web_search (Bing RSS + Google News + Wiki + HN + Tavily), web_fetch, http_get, calculator, get_datetime, ocr_image, voice_speak, memory_note, task_plan, list_skills, load_skill, mcp_list, mcp_call, dynamic `mcp__server__tool`
- **Skills**: SKILL.md frontmatter layout under `data/skills/`; builtins research / coding / daily; auto-match by triggers
- **MCP**: HTTP JSON-RPC client; config `mcp_servers.json`
- **Context compression**: soft/hard token budgets; trim tool results; drop oldest turns; **never orphan tool pairs**; no hallucinated tool results in summary
- **Tool-call formats**: OpenAI function-calling stream; text XML `<tool_call>` for Codex / non-FC models; JSON args repair
- **Guards**: doom-loop (3× same fingerprint), max iterations, last-turn force final

## Host QA APIs

- `POST /api/mobile/local/tools` `{name,args}`
- `GET /api/mobile/local/skills`
- `GET|POST /api/mobile/local/mcp`
- `GET|POST /api/mobile/local/agent_config`

## Verified (sandbox)

- KERNEL SMOKE OK: 13 schemas, 3 skills, web_search live, compress 26940→2315 tok orphan=0
- Host live: list_skills / task_plan / web_search / agent_config OK
- Unit tests: 14 passed
