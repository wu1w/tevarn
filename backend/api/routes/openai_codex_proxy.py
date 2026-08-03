"""本地 OpenAI 兼容代理：把 /v1/chat/completions 转到 ChatGPT Codex 订阅后端。

供应商 llm_base_url = http://127.0.0.1:8090/api/llm-proxy/openai-codex/v1
请求头 Authorization: Bearer <oauth access_token>
可选 ChatGPT-Account-Id。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/llm-proxy/openai-codex", tags=["openai-codex-proxy"])

UPSTREAM = "https://chatgpt.com/backend-api/codex"


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.headers.get("x-api-key") or "").strip()


def _account_id(request: Request) -> str:
    return (
        request.headers.get("chatgpt-account-id")
        or request.headers.get("ChatGPT-Account-Id")
        or ""
    ).strip()


def _messages_to_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """粗粒度转换 chat messages → Codex/Responses input items。"""
    items: list[dict[str, Any]] = []
    for m in messages or []:
        role = str(m.get("role") or "user")
        content = m.get("content")
        if isinstance(content, list):
            text_parts = []
            for p in content:
                if isinstance(p, dict) and p.get("type") in ("text", "input_text"):
                    text_parts.append(str(p.get("text") or ""))
                elif isinstance(p, str):
                    text_parts.append(p)
            content = "\n".join(text_parts)
        text = str(content or "")
        if role == "system":
            items.append(
                {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": text}],
                }
            )
        elif role == "assistant":
            items.append(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                }
            )
        else:
            items.append(
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                }
            )
    return items


def _extract_text_from_codex(data: dict[str, Any]) -> str:
    # 兼容多种 responses 形态
    if isinstance(data.get("output_text"), str) and data["output_text"]:
        return data["output_text"]
    out = data.get("output") or data.get("response") or []
    chunks: list[str] = []
    if isinstance(out, list):
        for item in out:
            if not isinstance(item, dict):
                continue
            if item.get("type") in ("message", "output_message"):
                for c in item.get("content") or []:
                    if isinstance(c, dict) and c.get("type") in (
                        "output_text",
                        "text",
                    ):
                        chunks.append(str(c.get("text") or ""))
            if item.get("type") == "text" and item.get("text"):
                chunks.append(str(item.get("text")))
    if chunks:
        return "".join(chunks)
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        msg = (choices[0] or {}).get("message") or {}
        return str(msg.get("content") or "")
    return str(data.get("content") or data.get("message") or "")


# Codex / ChatGPT 订阅路径模型（与 settings preset 保持一致；非 platform API 目录）
CODEX_OAUTH_MODELS = [
    "gpt-5.6",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.4",
    "gpt-5.3-codex",
    "gpt-5.2-codex",
    "gpt-5.1-codex",
    "o3",
    "o4-mini",
    "gpt-4.1",
    "gpt-4o",
]
DEFAULT_CODEX_MODEL = "gpt-5.6"


@router.get("/v1/models")
async def list_models():
    """订阅路径常用 Codex/GPT 模型列表（静态，跟进 OpenAI Codex 公开型号）。"""
    return {
        "object": "list",
        "data": [
            {"id": m, "object": "model", "owned_by": "openai-chatgpt-oauth"}
            for m in CODEX_OAUTH_MODELS
        ],
    }


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    token = _bearer(request)
    if not token:
        return JSONResponse(
            {"error": {"message": "missing OAuth Bearer token", "type": "auth_error"}},
            status_code=401,
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"error": {"message": "invalid JSON body", "type": "invalid_request"}},
            status_code=400,
        )

    model = str(body.get("model") or DEFAULT_CODEX_MODEL)
    messages = body.get("messages") or []
    stream = bool(body.get("stream"))
    temperature = body.get("temperature")
    max_tokens = body.get("max_tokens") or body.get("max_completion_tokens")

    # Codex responses 风格请求
    payload: dict[str, Any] = {
        "model": model,
        "input": _messages_to_input(messages if isinstance(messages, list) else []),
        "stream": stream,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_output_tokens"] = max_tokens

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if stream else "application/json",
        "OpenAI-Beta": "responses=experimental",
    }
    aid = _account_id(request)
    if aid:
        headers["ChatGPT-Account-Id"] = aid

    url = f"{UPSTREAM}/responses"
    timeout = aiohttp.ClientTimeout(total=300)

    from backend.core.outbound_http import outbound_session

    if not stream:
        try:
            async with outbound_session(timeout=timeout) as (session, proxy):
                async with session.post(
                    url, headers=headers, json=payload, proxy=proxy
                ) as resp:
                    text = await resp.text()
                    try:
                        data = json.loads(text) if text else {}
                    except Exception:
                        data = {"raw": text[:2000]}
                    if resp.status >= 400:
                        return JSONResponse(
                            {
                                "error": {
                                    "message": data.get("error")
                                    or data.get("detail")
                                    or text[:500],
                                    "type": "upstream_error",
                                    "status": resp.status,
                                }
                            },
                            status_code=resp.status,
                        )
                    content = _extract_text_from_codex(
                        data if isinstance(data, dict) else {}
                    )
                    return {
                        "id": str(
                            (data or {}).get("id")
                            or (data or {}).get("response_id")
                            or "codex-oauth"
                        ),
                        "object": "chat.completion",
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": content},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": (data or {}).get("usage") or {},
                    }
        except Exception as e:
            logger.warning("openai-codex proxy failed: %s", e)
            return JSONResponse(
                {"error": {"message": str(e), "type": "proxy_error"}},
                status_code=502,
            )

    # streaming：尽量透传/包装为 SSE chat.completion.chunk
    async def _gen():
        try:
            async with outbound_session(timeout=timeout) as (session, proxy):
                async with session.post(
                    url, headers=headers, json=payload, proxy=proxy
                ) as resp:
                    if resp.status >= 400:
                        err = await resp.text()
                        chunk = {
                            "id": "codex-err",
                            "object": "chat.completion.chunk",
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "content": f"[Codex OAuth error {resp.status}] {err[:300]}"
                                    },
                                    "finish_reason": "stop",
                                }
                            ],
                        }
                        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                    async for raw in resp.content:
                        line = raw.decode("utf-8", errors="replace").strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data_s = line[5:].strip()
                        if data_s == "[DONE]":
                            yield "data: [DONE]\n\n"
                            break
                        try:
                            ev = json.loads(data_s)
                        except Exception:
                            continue
                        # 抽取增量文本
                        delta = ""
                        if isinstance(ev, dict):
                            if ev.get("type") in (
                                "response.output_text.delta",
                                "response.output_text.delta.event",
                            ):
                                delta = str(ev.get("delta") or "")
                            elif ev.get("delta"):
                                d = ev.get("delta")
                                if isinstance(d, str):
                                    delta = d
                                elif isinstance(d, dict):
                                    delta = str(
                                        d.get("text") or d.get("content") or ""
                                    )
                        if not delta:
                            continue
                        chunk = {
                            "id": "codex-stream",
                            "object": "chat.completion.chunk",
                            "model": model,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": delta},
                                    "finish_reason": None,
                                }
                            ],
                        }
                        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
        except Exception as e:
            chunk = {
                "id": "codex-err",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": f"[proxy stream error] {e}"},
                        "finish_reason": "stop",
                    }
                ],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")
