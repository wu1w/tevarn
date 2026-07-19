"""Takton Code ↔ Desktop bridge API.

Exposes models / chat / skills / tools / MCP / RAG under /bridge/v1/*
so the independent Takton Code CLI can call the full desktop backend.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.api.dependencies import get_current_user
from backend.core.config import settings
from backend.schemas.user import UserRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bridge/v1", tags=["bridge"])


class ChatMessageIn(BaseModel):
    role: str
    content: str | None = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


class ChatRequestIn(BaseModel):
    model: str | None = None
    messages: list[ChatMessageIn]
    tools: list[dict[str, Any]] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    session_id: str | None = None


class ToolInvokeIn(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None
    project_root: str | None = None


class RAGQueryIn(BaseModel):
    query: str
    top_k: int = 5
    collection: str | None = None


def _tool_call_to_dict(tc: Any) -> dict[str, Any]:
    if tc is None:
        return {}
    if isinstance(tc, dict):
        return tc
    if hasattr(tc, "model_dump"):
        return tc.model_dump()
    if hasattr(tc, "dict"):
        return tc.dict()
    # duck type
    fn = getattr(tc, "function", None)
    return {
        "id": getattr(tc, "id", None) or f"call_{uuid.uuid4().hex[:8]}",
        "type": getattr(tc, "type", "function"),
        "function": {
            "name": getattr(fn, "name", None) if fn is not None else None,
            "arguments": getattr(fn, "arguments", "{}") if fn is not None else "{}",
        },
    }


@router.get("/health")
async def bridge_health(
    current_user: UserRead = Depends(get_current_user),
) -> dict[str, Any]:
    return {
        "ok": True,
        "enabled": True,
        "version": getattr(settings, "version", None) or "0.2.1",
        "product": "takton",
        "user": str(current_user.id),
        "capabilities": ["models", "skills", "tools", "mcp", "rag", "sessions", "settings"],
        "llm_provider": getattr(settings, "llm_provider", None),
        "llm_model": getattr(settings, "llm_model", None),
    }


@router.get("/models")
async def bridge_list_models(
    current_user: UserRead = Depends(get_current_user),
) -> dict[str, Any]:
    """List models from catalog + active provider model."""
    models: list[dict[str, Any]] = []
    active = getattr(settings, "llm_model", None) or "default"
    provider = getattr(settings, "llm_provider", None) or "unknown"
    ctx = getattr(settings, "context_window", None) or getattr(settings, "llm_context_window", None)
    models.append(
        {
            "id": active,
            "name": active,
            "provider": provider,
            "context_window": ctx,
            "description": "Active desktop LLM",
        }
    )
    try:
        from backend.repositories.setting_repo import AsyncSettingRepository

        repo = AsyncSettingRepository()
        raw = await repo.get_by_key("model_catalog")
        catalog = None
        if raw is not None:
            val = getattr(raw, "value", raw)
            if isinstance(val, str):
                try:
                    catalog = json.loads(val)
                except json.JSONDecodeError:
                    catalog = None
            elif isinstance(val, dict):
                catalog = val
        if isinstance(catalog, dict):
            providers = catalog.get("providers") or catalog.get("items") or []
            if isinstance(providers, dict):
                providers = list(providers.values())
            for p in providers:
                if not isinstance(p, dict):
                    continue
                pname = p.get("name") or p.get("id") or "provider"
                cached = p.get("cached_models") or p.get("models") or []
                if isinstance(cached, list):
                    for m in cached[:80]:
                        mid = m if isinstance(m, str) else (m.get("id") or m.get("name"))
                        if not mid:
                            continue
                        if any(x["id"] == mid for x in models):
                            continue
                        models.append(
                            {
                                "id": mid,
                                "name": mid if isinstance(m, str) else (m.get("name") or mid),
                                "provider": pname,
                                "context_window": None
                                if isinstance(m, str)
                                else m.get("context_window"),
                                "description": None
                                if isinstance(m, str)
                                else m.get("description"),
                            }
                        )
    except Exception as e:  # noqa: BLE001
        logger.debug("bridge models catalog read failed: %s", e)

    return {"data": models, "object": "list"}


@router.post("/chat/completions")
async def bridge_chat(
    body: ChatRequestIn,
    current_user: UserRead = Depends(get_current_user),
) -> dict[str, Any]:
    """OpenAI-shaped chat completions using Desktop LLM stack."""
    from backend.services.llm.factory import LLMServiceFactory

    messages: list[dict[str, Any]] = []
    for m in body.messages:
        d: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_calls:
            d["tool_calls"] = m.tool_calls
            if not d.get("content"):
                d["content"] = None
        if m.tool_call_id:
            d["tool_call_id"] = m.tool_call_id
        if m.name:
            d["name"] = m.name
        messages.append(d)

    # optional session snapshot lock
    svc = None
    if body.session_id:
        try:
            from backend.repositories.session_repo import AsyncSessionRepository

            srepo = AsyncSessionRepository()
            sid = uuid.UUID(str(body.session_id))
            sess = await srepo.get_by_id(sid)
            if sess is not None:
                cfg = getattr(sess, "config", None) or {}
                if isinstance(cfg, str):
                    try:
                        cfg = json.loads(cfg)
                    except json.JSONDecodeError:
                        cfg = {}
                snap = (cfg or {}).get("llm") if isinstance(cfg, dict) else None
                if snap:
                    svc = LLMServiceFactory.get_service_for_snapshot(snap)
        except Exception as e:  # noqa: BLE001
            logger.debug("bridge session snapshot skip: %s", e)

    if svc is None:
        # honor explicit model override via ephemeral snapshot when possible
        if body.model:
            snap = {
                "provider": getattr(settings, "llm_provider", "openai-compatible"),
                "model": body.model,
                "base_url": getattr(settings, "llm_base_url", None),
                "api_key": getattr(settings, "llm_api_key", None),
                "max_tokens": body.max_tokens or getattr(settings, "max_tokens", 4096),
                "temperature": body.temperature
                if body.temperature is not None
                else getattr(settings, "temperature", 0.7),
            }
            try:
                svc = LLMServiceFactory.get_service_for_snapshot(snap)
            except Exception:  # noqa: BLE001
                svc = LLMServiceFactory.get_service()
        else:
            svc = LLMServiceFactory.get_service()

    try:
        resp = await svc.chat_complete(messages, tools=body.tools)
    except Exception as e:  # noqa: BLE001
        logger.exception("bridge chat failed")
        raise HTTPException(status_code=502, detail=f"LLM error: {e}") from e

    content = getattr(resp, "content", None) or ""
    # reasoning_content if present on chunks path — some services put thinking only
    reasoning = getattr(resp, "reasoning_content", None)
    tcs_raw = getattr(resp, "tool_calls", None) or []
    tcs = [_tool_call_to_dict(tc) for tc in tcs_raw if tc]
    # filter empty
    tcs = [t for t in tcs if (t.get("function") or {}).get("name")]

    message: dict[str, Any] = {"role": "assistant", "content": content or None}
    if reasoning:
        message["reasoning_content"] = reasoning
    if tcs:
        message["tool_calls"] = tcs
        if not message.get("content"):
            message["content"] = None

    return {
        "id": f"chatcmpl-bridge-{uuid.uuid4().hex[:10]}",
        "object": "chat.completion",
        "model": body.model or getattr(settings, "llm_model", "default"),
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": getattr(resp, "finish_reason", None) or ("tool_calls" if tcs else "stop"),
            }
        ],
    }


@router.get("/skills")
async def bridge_list_skills(
    current_user: UserRead = Depends(get_current_user),
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    try:
        from backend.repositories.skill_repo import AsyncSkillRepository

        repo = AsyncSkillRepository()
        rows = await repo.list_all() if hasattr(repo, "list_all") else await repo.list()
        for s in rows or []:
            items.append(
                {
                    "name": getattr(s, "name", None) or str(getattr(s, "id", "")),
                    "description": getattr(s, "description", "") or "",
                    "enabled": bool(getattr(s, "enabled", True)),
                    "source": "builtin",
                    "prompt_injection": getattr(s, "prompt_template", None)
                    or getattr(s, "system_prompt", None),
                }
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("bridge list skills db: %s", e)

    # also runtime skill registry
    try:
        from backend.skills import SkillRegistry

        for name, skill in getattr(SkillRegistry, "_skills", {}).items() if hasattr(SkillRegistry, "_skills") else []:
            if any(i["name"] == name for i in items):
                continue
            items.append(
                {
                    "name": name,
                    "description": getattr(skill, "description", "") or "",
                    "enabled": True,
                    "source": "builtin",
                    "prompt_injection": None,
                }
            )
        # alternate API
        if hasattr(SkillRegistry, "list_skills"):
            for skill in SkillRegistry.list_skills():  # type: ignore[attr-defined]
                name = getattr(skill, "name", None)
                if not name or any(i["name"] == name for i in items):
                    continue
                items.append(
                    {
                        "name": name,
                        "description": getattr(skill, "description", "") or "",
                        "enabled": True,
                        "source": "builtin",
                    }
                )
    except Exception as e:  # noqa: BLE001
        logger.debug("bridge skill registry: %s", e)

    # prompt skills on disk
    try:
        from backend.services.prompt_skill_loader import PromptSkillLoader  # type: ignore

        loader = PromptSkillLoader()
        for s in getattr(loader, "list_all", lambda: [])() or []:
            name = s.get("name") if isinstance(s, dict) else getattr(s, "name", None)
            if not name:
                continue
            if any(i["name"] == name for i in items):
                continue
            items.append(
                {
                    "name": name,
                    "description": (s.get("description") if isinstance(s, dict) else "") or "",
                    "enabled": True,
                    "source": "store",
                    "prompt_injection": s.get("body") if isinstance(s, dict) else None,
                }
            )
    except Exception:  # noqa: BLE001
        pass

    return {"skills": items, "data": items}


@router.get("/tools")
async def bridge_list_tools(
    current_user: UserRead = Depends(get_current_user),
) -> dict[str, Any]:
    tools: list[dict[str, Any]] = []
    try:
        from backend.tools.registry import ToolRegistry as UnifiedToolRegistry

        schemas = UnifiedToolRegistry.get_tools_schema()
        for sch in schemas or []:
            fn = sch.get("function") if isinstance(sch, dict) else None
            if not fn:
                # already flat?
                name = sch.get("name") if isinstance(sch, dict) else None
                if not name:
                    continue
                tools.append(
                    {
                        "name": name,
                        "description": sch.get("description") or "",
                        "parameters_schema": sch.get("parameters") or {},
                        "risk_level": "low",
                        "source": "desktop",
                    }
                )
                continue
            tools.append(
                {
                    "name": fn.get("name"),
                    "description": fn.get("description") or "",
                    "parameters_schema": fn.get("parameters") or {},
                    "risk_level": "low",
                    "source": "desktop",
                }
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("bridge tools schema: %s", e)

    return {"tools": tools, "data": tools}


@router.post("/tools/invoke")
async def bridge_invoke_tool(
    body: ToolInvokeIn,
    current_user: UserRead = Depends(get_current_user),
) -> dict[str, Any]:
    name = (body.name or "").strip()
    if not name:
        return {"ok": False, "output": "", "error": "name required"}

    args = dict(body.arguments or {})
    # inject identity context for skills that accept it
    args.setdefault("user_id", str(current_user.id))
    if body.session_id:
        args.setdefault("_session_id", body.session_id)
    if body.project_root:
        args.setdefault("project_root", body.project_root)
        args.setdefault("_project_root", body.project_root)

    # 1) unified tool registry
    try:
        from backend.tools.registry import ToolRegistry as UnifiedToolRegistry

        if UnifiedToolRegistry.get(name) is not None:
            out = await UnifiedToolRegistry.execute(name, args)
            return {"ok": not str(out).startswith("[Error]"), "output": str(out), "error": None}
    except Exception as e:  # noqa: BLE001
        logger.debug("unified tool miss %s: %s", name, e)

    # 2) skill registry
    try:
        from backend.skills import SkillRegistry

        skill = None
        if hasattr(SkillRegistry, "get"):
            skill = SkillRegistry.get(name)
        if skill is None and hasattr(SkillRegistry, "get_skill"):
            skill = SkillRegistry.get_skill(name)
        if skill is not None:
            out = await skill.execute(**args)
            return {"ok": True, "output": str(out), "error": None}
    except TypeError as e:
        # kwargs mismatch — retry with filtered
        try:
            import inspect

            sig = inspect.signature(skill.execute)  # type: ignore[union-attr]
            accepted = {k: v for k, v in args.items() if k in sig.parameters or any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())}
            out = await skill.execute(**accepted)  # type: ignore[union-attr]
            return {"ok": True, "output": str(out), "error": None}
        except Exception as e2:  # noqa: BLE001
            return {"ok": False, "output": "", "error": f"{e}; {e2}"}
    except Exception as e:  # noqa: BLE001
        logger.debug("skill invoke %s: %s", name, e)

    # 3) DB tool by name
    try:
        from backend.repositories.tool_repo import AsyncToolRepository
        from backend.services.tools import ToolRegistry

        repo = AsyncToolRepository()
        tools = await repo.list_all() if hasattr(repo, "list_all") else []
        match = next((t for t in tools if getattr(t, "name", None) == name), None)
        if match is not None:
            out = await ToolRegistry.execute_tool(match, args)
            return {"ok": not str(out).startswith("[Error]"), "output": str(out), "error": None}
    except Exception as e:  # noqa: BLE001
        logger.debug("db tool invoke: %s", e)

    return {"ok": False, "output": "", "error": f"tool not found: {name}"}


@router.get("/mcp")
async def bridge_list_mcp(
    current_user: UserRead = Depends(get_current_user),
) -> dict[str, Any]:
    servers: list[dict[str, Any]] = []
    try:
        from backend.repositories import mcp as mcp_mod  # type: ignore
    except Exception:
        mcp_mod = None
    try:
        # common path: models via session
        from backend.database import async_session_factory
        from sqlalchemy import text

        async with async_session_factory() as session:
            try:
                rows = (
                    await session.execute(
                        text("SELECT name, enabled, status FROM mcp_servers")
                    )
                ).mappings().all()
                for r in rows:
                    servers.append(
                        {
                            "name": r.get("name"),
                            "status": "enabled" if r.get("enabled") else (r.get("status") or "disabled"),
                            "tools": [],
                        }
                    )
            except Exception:
                # table may differ
                rows = (
                    await session.execute(text("SELECT name FROM mcp_servers"))
                ).fetchall()
                for r in rows:
                    servers.append({"name": r[0], "status": "unknown", "tools": []})
    except Exception as e:  # noqa: BLE001
        logger.debug("bridge mcp list: %s", e)
        try:
            from backend.api.routes import mcp as mcp_routes

            # fallback empty rather than crash
            _ = mcp_routes
        except Exception:
            pass

    return {"servers": servers, "data": servers}


@router.post("/rag/search")
async def bridge_rag_search(
    body: RAGQueryIn,
    current_user: UserRead = Depends(get_current_user),
) -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    try:
        from backend.skills.builtins.rag_skill import SearchKnowledgeBaseSkill

        skill = SearchKnowledgeBaseSkill()
        raw = await skill.execute(
            query=body.query,
            top_k=body.top_k,
            collection=body.collection,
            user_id=str(current_user.id),
        )
        # skill returns string (json or text)
        text = str(raw)
        try:
            data = json.loads(text)
            if isinstance(data, dict) and data.get("ok") is False:
                return {"hits": [], "results": [], "error": data}
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        hits.append(
                            {
                                "content": item.get("content") or item.get("text") or json.dumps(item, ensure_ascii=False),
                                "score": item.get("score"),
                                "source": item.get("source") or item.get("document"),
                                "meta": item,
                            }
                        )
            elif isinstance(data, dict):
                results = data.get("results") or data.get("hits") or data.get("documents") or []
                for item in results:
                    if isinstance(item, str):
                        hits.append({"content": item, "score": None, "source": None, "meta": {}})
                    elif isinstance(item, dict):
                        hits.append(
                            {
                                "content": item.get("content") or item.get("text") or "",
                                "score": item.get("score"),
                                "source": item.get("source"),
                                "meta": item,
                            }
                        )
                if not hits and data.get("content"):
                    hits.append({"content": str(data["content"]), "score": None, "source": None, "meta": data})
        except json.JSONDecodeError:
            if text.strip():
                hits.append({"content": text[:8000], "score": None, "source": "rag", "meta": {}})
    except Exception as e:  # noqa: BLE001
        logger.exception("bridge rag")
        return {"hits": [], "results": [], "error": str(e)}

    return {"hits": hits, "results": hits}


@router.get("/settings")
async def bridge_settings(
    current_user: UserRead = Depends(get_current_user),
) -> dict[str, Any]:
    return {
        "llm_provider": getattr(settings, "llm_provider", None),
        "llm_model": getattr(settings, "llm_model", None),
        "llm_base_url": getattr(settings, "llm_base_url", None),
        "temperature": getattr(settings, "temperature", None),
        "max_tokens": getattr(settings, "max_tokens", None),
        "single_user_mode": getattr(settings, "single_user_mode", None),
        "user_id": str(current_user.id),
    }
