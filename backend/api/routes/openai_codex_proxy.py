"""本地 OpenAI 兼容代理：/v1/chat/completions → ChatGPT Codex 订阅 Responses API。

上游：``https://chatgpt.com/backend-api/codex/responses``
供应商 base_url：``http://127.0.0.1:8090/api/llm-proxy/openai-codex/v1``

## Codex OAuth 请求格式（实测 + 社区对照）

ChatGPT Codex **不是** 完整 Platform Responses API。硬约束：

| 字段 | 规则 |
|------|------|
| ``store`` | **必须** ``false``（缺省/true → 400 ``Store must be set to false``） |
| ``stream`` | 建议 ``true``（部分账号 ``stream:false`` 会 400） |
| ``temperature`` / ``top_p`` | **禁止** |
| ``max_output_tokens`` / ``max_tokens`` | **禁止**（Codex 订阅路径会 400；Hermes 对 is_codex_backend 也不发） |
| ``user`` / ``metadata`` / ``cache_control`` | **禁止** |
| 允许 | ``model``, ``input``, ``instructions``, ``stream``, ``store``, |
|      | ``tools``, ``tool_choice``, ``reasoning``, ``include``, |
|      | ``previous_response_id``, ``truncation`` |

参考：openai/codex#26173 · litellm#21193 · hermes-agent codex transport
（``max_tokens is not None and not is_codex_backend`` 才写 max_output_tokens）。
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/llm-proxy/openai-codex", tags=["openai-codex-proxy"])

UPSTREAM = "https://chatgpt.com/backend-api/codex"

# ChatGPT Codex Responses 白名单（allowlist，勿透传 chat.completions 杂字段）
_CODEX_BODY_ALLOW = frozenset(
    {
        "model",
        "input",
        "instructions",
        "stream",
        "store",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "reasoning",
        "include",
        "previous_response_id",
        "truncation",
        "prompt_cache_key",
    }
)

# 明确丢弃（含别名）
_CODEX_BODY_DENY = frozenset(
    {
        "temperature",
        "top_p",
        "top_k",
        "presence_penalty",
        "frequency_penalty",
        "logit_bias",
        "user",
        "n",
        "stop",
        "seed",
        "logprobs",
        "top_logprobs",
        "max_tokens",
        "max_completion_tokens",
        "max_output_tokens",
        "metadata",
        "response_format",
        "stream_options",
        "service_tier",
        "modalities",
        "audio",
        "prediction",
        "web_search_options",
        "context_management",
        "prompt_cache_retention",
        "safety_identifier",
        "messages",  # 已转成 input
        "cache_control",
    }
)


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


def _as_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, dict):
                if p.get("type") in ("text", "input_text", "output_text"):
                    parts.append(str(p.get("text") or ""))
                elif "text" in p:
                    parts.append(str(p.get("text") or ""))
            elif isinstance(p, str):
                parts.append(p)
        return "\n".join(parts)
    return str(content)


def _split_system_and_messages(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Codex 偏好 instructions=system；其余进 input。"""
    instructions_parts: list[str] = []
    rest: list[dict[str, Any]] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "user")
        if role == "system":
            t = _as_text(m.get("content")).strip()
            if t:
                instructions_parts.append(t)
            continue
        rest.append(m)
    return "\n\n".join(instructions_parts).strip(), rest


def _messages_to_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """chat messages → Codex/Responses input items。"""
    items: list[dict[str, Any]] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "user")
        text = _as_text(m.get("content"))

        # tool result
        if role == "tool":
            call_id = str(m.get("tool_call_id") or m.get("id") or "")
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id or "call_unknown",
                    "output": text,
                }
            )
            continue

        if role == "assistant":
            # function calls on assistant
            tcs = m.get("tool_calls")
            if isinstance(tcs, list) and tcs:
                if text.strip():
                    items.append(
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": text}],
                        }
                    )
                for tc in tcs:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                    name = str((fn or {}).get("name") or tc.get("name") or "")
                    args = (fn or {}).get("arguments", tc.get("arguments", "{}"))
                    if not isinstance(args, str):
                        try:
                            args = json.dumps(args, ensure_ascii=False)
                        except Exception:
                            args = "{}"
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": str(tc.get("id") or f"call_{name or 'tool'}"),
                            "name": name,
                            "arguments": args,
                        }
                    )
                continue
            items.append(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                }
            )
            continue

        # user / developer / other
        r = "user" if role == "user" else ("developer" if role == "developer" else "user")
        items.append(
            {
                "type": "message",
                "role": r,
                "content": [{"type": "input_text", "text": text}],
            }
        )
    return items


def _convert_tools(tools: Any) -> list[dict[str, Any]] | None:
    """OpenAI chat tools → Responses function tools（仅 type=function）。"""
    if not isinstance(tools, list) or not tools:
        return None
    out: list[dict[str, Any]] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else None
        if t.get("type") == "function" and fn:
            out.append(
                {
                    "type": "function",
                    "name": str(fn.get("name") or ""),
                    "description": str(fn.get("description") or ""),
                    "parameters": fn.get("parameters")
                    if isinstance(fn.get("parameters"), dict)
                    else {"type": "object", "properties": {}},
                }
            )
        elif t.get("type") == "function" and t.get("name"):
            # already responses-shaped
            out.append(t)
        elif t.get("name") and "parameters" in t:
            out.append(
                {
                    "type": "function",
                    "name": str(t.get("name") or ""),
                    "description": str(t.get("description") or ""),
                    "parameters": t.get("parameters")
                    if isinstance(t.get("parameters"), dict)
                    else {"type": "object", "properties": {}},
                }
            )
    return out or None


def build_codex_oauth_payload(body: dict[str, Any]) -> dict[str, Any]:
    """把 OpenAI chat.completions body 归一化为 Codex OAuth Responses body。

    严格白名单；绝不透传 temperature / max_*_tokens / cache_control 等。
    """
    model = str(body.get("model") or DEFAULT_CODEX_MODEL).strip() or DEFAULT_CODEX_MODEL
    messages = body.get("messages") if isinstance(body.get("messages"), list) else []
    instructions, rest = _split_system_and_messages(messages)
    # 若上游已用 instructions 字段，合并
    extra_inst = str(body.get("instructions") or "").strip()
    if extra_inst:
        instructions = (
            f"{instructions}\n\n{extra_inst}".strip() if instructions else extra_inst
        )

    # Codex 订阅路径：强制 stream=true + store=false（见社区实测）
    stream = True
    if body.get("stream") is False:
        # 仍强制 true，避免 400；客户端仍可走我们的 SSE 包装
        logger.debug("codex oauth: forcing stream=true (client asked false)")

    payload: dict[str, Any] = {
        "model": model,
        "input": _messages_to_input(rest),
        "stream": stream,
        "store": False,
    }
    if instructions:
        payload["instructions"] = instructions

    tools = _convert_tools(body.get("tools"))
    if tools:
        payload["tools"] = tools
        tc = body.get("tool_choice")
        if tc is not None:
            payload["tool_choice"] = tc
        else:
            payload["tool_choice"] = "auto"
        payload["parallel_tool_calls"] = True

    # reasoning（可选，gpt-5.x 系）
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict) and reasoning:
        effort = reasoning.get("effort") or reasoning.get("reasoning_effort")
        if effort:
            payload["reasoning"] = {
                "effort": str(effort),
                "summary": str(reasoning.get("summary") or "auto"),
            }
    else:
        reff = body.get("reasoning_effort")
        if reff:
            payload["reasoning"] = {"effort": str(reff), "summary": "auto"}

    # 二次保险：剥掉任何误入的禁止字段
    for k in list(payload.keys()):
        if k in _CODEX_BODY_DENY or k not in _CODEX_BODY_ALLOW:
            if k not in _CODEX_BODY_ALLOW:
                payload.pop(k, None)

    # 强制再写一遍硬约束（防被覆盖）
    payload["store"] = False
    payload["stream"] = True
    return payload


def _extract_text_from_codex(data: dict[str, Any]) -> str:
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


# Codex / ChatGPT 订阅路径模型
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
    """订阅路径常用 Codex/GPT 模型列表。"""
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
    if not isinstance(body, dict):
        return JSONResponse(
            {"error": {"message": "body must be object", "type": "invalid_request"}},
            status_code=400,
        )

    payload = build_codex_oauth_payload(body)
    model = str(payload.get("model") or DEFAULT_CODEX_MODEL)
    # 客户端是否期望 SSE；上游始终 stream=true，非流式再拼装完整响应
    client_wants_stream = bool(body.get("stream", True))

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "OpenAI-Beta": "responses=experimental",
    }
    aid = _account_id(request)
    if aid:
        headers["ChatGPT-Account-Id"] = aid

    url = f"{UPSTREAM}/responses"
    timeout = aiohttp.ClientTimeout(total=300)

    from backend.core.outbound_http import outbound_session

    logger.info(
        "codex oauth → upstream model=%s keys=%s input_items=%s has_tools=%s",
        model,
        sorted(payload.keys()),
        len(payload.get("input") or []),
        bool(payload.get("tools")),
    )

    if not client_wants_stream:
        # 上游仍用 stream=true 收集完整输出（Codex 对 stream:false 不友好）
        try:
            async with outbound_session(timeout=timeout) as (session, proxy):
                async with session.post(
                    url, headers=headers, json=payload, proxy=proxy
                ) as resp:
                    if resp.status >= 400:
                        err = await resp.text()
                        logger.warning(
                            "codex oauth upstream %s: %s", resp.status, err[:400]
                        )
                        return JSONResponse(
                            {
                                "error": {
                                    "message": err[:800],
                                    "type": "upstream_error",
                                    "status": resp.status,
                                }
                            },
                            status_code=resp.status,
                        )
                    # 聚合 SSE → 完整文本
                    content_parts: list[str] = []
                    usage: dict[str, Any] = {}
                    resp_id = "codex-oauth"
                    async for raw in resp.content:
                        line = raw.decode("utf-8", errors="replace").strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data_s = line[5:].strip()
                        if data_s == "[DONE]":
                            break
                        try:
                            ev = json.loads(data_s)
                        except Exception:
                            continue
                        if not isinstance(ev, dict):
                            continue
                        if ev.get("id"):
                            resp_id = str(ev.get("id"))
                        if ev.get("type") in (
                            "response.output_text.delta",
                            "response.output_text.delta.event",
                        ):
                            content_parts.append(str(ev.get("delta") or ""))
                        elif isinstance(ev.get("response"), dict):
                            r = ev["response"]
                            if r.get("id"):
                                resp_id = str(r["id"])
                            if isinstance(r.get("usage"), dict):
                                usage = r["usage"]
                            # completed payload
                            if ev.get("type") in (
                                "response.completed",
                                "response.done",
                            ):
                                t = _extract_text_from_codex(r)
                                if t and not content_parts:
                                    content_parts.append(t)
                    content = "".join(content_parts)
                    return {
                        "id": resp_id,
                        "object": "chat.completion",
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": content,
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": usage or {},
                    }
        except Exception as e:
            logger.warning("openai-codex proxy failed: %s", e)
            return JSONResponse(
                {"error": {"message": str(e), "type": "proxy_error"}},
                status_code=502,
            )

    async def _gen():
        try:
            async with outbound_session(timeout=timeout) as (session, proxy):
                async with session.post(
                    url, headers=headers, json=payload, proxy=proxy
                ) as resp:
                    if resp.status >= 400:
                        err = await resp.text()
                        logger.warning(
                            "codex oauth stream upstream %s: %s",
                            resp.status,
                            err[:400],
                        )
                        chunk = {
                            "id": "codex-err",
                            "object": "chat.completion.chunk",
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "content": (
                                            f"[Codex OAuth error {resp.status}] "
                                            f"{err[:400]}"
                                        )
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
