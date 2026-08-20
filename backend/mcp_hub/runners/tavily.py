"""Builtin Tavily MCP (stdio). Runs on Tevarn's Python — no Node/npx."""

from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SEARCH_URL = "https://api.tavily.com/search"
EXTRACT_URL = "https://api.tavily.com/extract"


def _api_key() -> str:
    return (os.environ.get("TAVILY_API_KEY") or "").strip()


def _post(url: str, payload: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
    key = _api_key()
    if not key:
        raise RuntimeError("TAVILY_API_KEY is not set")
    body = dict(payload)
    body.setdefault("api_key", key)
    req = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"Tavily HTTP {e.code}: {detail}") from e
    except URLError as e:
        raise RuntimeError(f"Tavily network error: {e.reason}") from e
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {"raw": raw}
    return data if isinstance(data, dict) else {"raw": data}


def tavily_search(
    query: str,
    max_results: int = 5,
    search_depth: str = "basic",
    include_answer: bool = True,
) -> str:
    data = _post(
        SEARCH_URL,
        {
            "query": query,
            "max_results": max(1, min(int(max_results or 5), 20)),
            "search_depth": search_depth or "basic",
            "include_answer": bool(include_answer),
        },
    )
    return json.dumps(data, ensure_ascii=False)


def tavily_extract(urls: list[str] | str) -> str:
    if isinstance(urls, str):
        url_list = [u.strip() for u in urls.split(",") if u.strip()]
    else:
        url_list = [str(u).strip() for u in (urls or []) if str(u).strip()]
    if not url_list:
        raise RuntimeError("urls is required")
    data = _post(EXTRACT_URL, {"urls": url_list})
    return json.dumps(data, ensure_ascii=False)


def main() -> None:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

    async def on_list_tools(ctx, params) -> ListToolsResult:
        return ListToolsResult(
            tools=[
                Tool(
                    name="tavily_search",
                    description="Search the web with Tavily. Returns JSON results.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "max_results": {"type": "integer", "default": 5},
                            "search_depth": {
                                "type": "string",
                                "enum": ["basic", "advanced"],
                                "default": "basic",
                            },
                            "include_answer": {"type": "boolean", "default": True},
                        },
                        "required": ["query"],
                    },
                ),
                Tool(
                    name="tavily_extract",
                    description="Extract readable content from one or more URLs via Tavily.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "urls": {
                                "anyOf": [
                                    {"type": "string"},
                                    {"type": "array", "items": {"type": "string"}},
                                ]
                            }
                        },
                        "required": ["urls"],
                    },
                ),
            ]
        )

    async def on_call_tool(ctx, params) -> CallToolResult:
        name = getattr(params, "name", "") or ""
        args = dict(getattr(params, "arguments", None) or {})
        try:
            if name == "tavily_search":
                text = tavily_search(
                    query=str(args.get("query") or ""),
                    max_results=int(args.get("max_results") or 5),
                    search_depth=str(args.get("search_depth") or "basic"),
                    include_answer=bool(args.get("include_answer", True)),
                )
            elif name == "tavily_extract":
                text = tavily_extract(args.get("urls") or [])
            else:
                text = f"Unknown tool: {name}"
        except Exception as e:
            text = f"[Error] {e}"
        return CallToolResult(content=[TextContent(type="text", text=text)])

    server = Server("tavily", on_list_tools=on_list_tools, on_call_tool=on_call_tool)

    async def _run() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    import anyio

    anyio.run(_run)


if __name__ == "__main__":
    main()
