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


def _extract_tool_calls_from_codex(data: dict[str, Any]) -> list[dict[str, Any]]:
    """从 completed Responses payload 抽出 function_call → OpenAI tool_calls。"""
    out = data.get("output") or []
    if not isinstance(out, list):
        return []
    tool_calls: list[dict[str, Any]] = []
    for item in out:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in ("function_call", "custom_tool_call"):
            continue
        name = str(item.get("name") or "")
        if not name:
            continue
        args = item.get("arguments")
        if not isinstance(args, str):
            try:
                args = json.dumps(args or {}, ensure_ascii=False)
            except Exception:
                args = "{}"
        tool_calls.append(
            {
                "id": str(item.get("call_id") or item.get("id") or f"call_{name}"),
                "type": "function",
                "function": {"name": name, "arguments": args or "{}"},
            }
        )
    return tool_calls


def _map_responses_usage(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Responses usage → OpenAI chat.completions usage (for stream clients)."""
    if not isinstance(raw, dict) or not raw:
        return {}
    try:
        from backend.services.llm.usage_normalize import map_responses_usage_to_openai

        return map_responses_usage_to_openai(raw)
    except Exception:
        out: dict[str, Any] = dict(raw)
        if "input_tokens" in raw and "prompt_tokens" not in raw:
            try:
                out["prompt_tokens"] = int(raw.get("input_tokens") or 0)
            except (TypeError, ValueError):
                out["prompt_tokens"] = 0
        if "output_tokens" in raw and "completion_tokens" not in raw:
            try:
                out["completion_tokens"] = int(raw.get("output_tokens") or 0)
            except (TypeError, ValueError):
                out["completion_tokens"] = 0
        if isinstance(raw.get("input_tokens_details"), dict):
            out["prompt_tokens_details"] = dict(raw["input_tokens_details"])
            out["input_tokens_details"] = dict(raw["input_tokens_details"])
        return out


def _sse_chat_chunk(
    *,
    model: str,
    content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    finish_reason: str | None = None,
    chunk_id: str = "codex-stream",
    usage: dict[str, Any] | None = None,
) -> str:
    delta: dict[str, Any] = {}
    if content:
        delta["content"] = content
    if tool_calls:
        delta["tool_calls"] = tool_calls
    if content is None and tool_calls is None and finish_reason is None and not usage:
        delta = {}
    payload: dict[str, Any] = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    if isinstance(usage, dict) and usage:
        payload["usage"] = usage
        # OpenAI stream_options pattern: empty choices when usage-only is also ok;
        # keep finish chunk + usage on same or follow-up chunk for compat.
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class _CodexStreamToChat:
    """Responses SSE → OpenAI chat.completion.chunk（含 tool_calls）。

    可靠策略：
    - 文本边收边推
    - 工具只缓冲，在 completed 时**整包**发出完整 tool_calls
      （避免参数流式丢片 → 执行时 ``{}`` / required property 失败）
    """

    def __init__(self, model: str) -> None:
        self.model = model
        self.resp_id = "codex-stream"
        self._id_to_index: dict[str, int] = {}
        self._calls: dict[int, dict[str, str]] = {}
        self._next_index = 0
        self._finished = False
        self._usage: dict[str, Any] = {}

    def _idx_for(self, *, item_id: str = "", call_id: str = "", name: str = "") -> int:
        for key in (call_id, item_id):
            if key and key in self._id_to_index:
                idx = self._id_to_index[key]
                if name and not self._calls[idx].get("name"):
                    self._calls[idx]["name"] = name
                if call_id:
                    self._calls[idx]["id"] = call_id
                return idx
        idx = self._next_index
        self._next_index += 1
        if call_id:
            self._id_to_index[call_id] = idx
        if item_id:
            self._id_to_index[item_id] = idx
        self._calls[idx] = {
            "id": call_id or item_id or f"call_{idx}",
            "name": name or "",
            "arguments": "",
        }
        return idx

    def _merge_from_final_output(self, resp: dict[str, Any]) -> None:
        for tc in _extract_tool_calls_from_codex(resp):
            fn = tc.get("function") or {}
            name = str(fn.get("name") or "")
            call_id = str(tc.get("id") or "")
            args = str(fn.get("arguments") or "")
            if not name:
                continue
            idx = self._idx_for(call_id=call_id, name=name)
            self._calls[idx]["name"] = name
            if call_id:
                self._calls[idx]["id"] = call_id
            if args:
                self._calls[idx]["arguments"] = args

    def _emit_tools_and_finish(self) -> list[str]:
        out: list[str] = []
        ready: list[dict[str, Any]] = []
        for i in sorted(self._calls.keys()):
            e = self._calls[i]
            name = (e.get("name") or "").strip()
            if not name:
                continue
            args = (e.get("arguments") or "").strip() or "{}"
            try:
                parsed = json.loads(args)
                if not isinstance(parsed, dict):
                    args = json.dumps({"value": parsed}, ensure_ascii=False)
                else:
                    args = json.dumps(parsed, ensure_ascii=False)
            except Exception:
                args = json.dumps({"_raw": args}, ensure_ascii=False)
            ready.append(
                {
                    "index": len(ready),
                    "id": e.get("id") or f"call_{name}",
                    "type": "function",
                    "function": {"name": name, "arguments": args},
                }
            )
        if ready:
            logger.info(
                "codex oauth tools finalized n=%s names=%s arg_lens=%s",
                len(ready),
                [t["function"]["name"] for t in ready],
                [len(t["function"]["arguments"]) for t in ready],
            )
            out.append(
                _sse_chat_chunk(
                    model=self.model, tool_calls=ready, chunk_id=self.resp_id
                )
            )
            fr = "tool_calls"
        else:
            fr = "stop"
        usage = _map_responses_usage(self._usage) if self._usage else {}
        out.append(
            _sse_chat_chunk(
                model=self.model,
                finish_reason=fr,
                chunk_id=self.resp_id,
                usage=usage or None,
            )
        )
        # Separate usage-only chunk (OpenAI stream_options.include_usage style)
        if usage:
            out.append(
                _sse_chat_chunk(
                    model=self.model,
                    chunk_id=self.resp_id,
                    usage=usage,
                )
            )
            logger.info(
                "codex oauth usage mapped prompt=%s completion=%s details=%s",
                usage.get("prompt_tokens") or usage.get("input_tokens"),
                usage.get("completion_tokens") or usage.get("output_tokens"),
                usage.get("prompt_tokens_details") or usage.get("input_tokens_details"),
            )
        out.append("data: [DONE]\n\n")
        return out

    def feed(self, ev: dict[str, Any]) -> list[str]:
        if not isinstance(ev, dict):
            return []
        et = str(ev.get("type") or "")
        out: list[str] = []

        if isinstance(ev.get("response"), dict):
            rid = ev["response"].get("id")
            if rid:
                self.resp_id = str(rid)
        rid = ev.get("response_id")
        if rid:
            self.resp_id = str(rid)

        # 文本即时推
        if et in (
            "response.output_text.delta",
            "response.output_text.delta.event",
        ):
            delta = str(ev.get("delta") or "")
            if delta:
                out.append(
                    _sse_chat_chunk(
                        model=self.model, content=delta, chunk_id=self.resp_id
                    )
                )
            return out

        # 工具只缓冲
        if et in ("response.output_item.added", "response.output_item.done"):
            item = ev.get("item") if isinstance(ev.get("item"), dict) else {}
            if item.get("type") in ("function_call", "custom_tool_call"):
                item_id = str(item.get("id") or "")
                call_id = str(item.get("call_id") or item_id)
                name = str(item.get("name") or "")
                args = item.get("arguments")
                args_s = ""
                if isinstance(args, str):
                    args_s = args
                elif isinstance(args, dict):
                    try:
                        args_s = json.dumps(args, ensure_ascii=False)
                    except Exception:
                        args_s = ""
                idx = self._idx_for(item_id=item_id, call_id=call_id, name=name)
                if name:
                    self._calls[idx]["name"] = name
                if call_id:
                    self._calls[idx]["id"] = call_id
                if args_s:
                    self._calls[idx]["arguments"] = args_s
            return out

        if et in (
            "response.function_call_arguments.delta",
            "response.custom_tool_call_input.delta",
        ):
            item_id = str(ev.get("item_id") or "")
            call_id = str(ev.get("call_id") or "")
            raw_delta = ev.get("delta")
            if isinstance(raw_delta, dict):
                delta = str(
                    raw_delta.get("partial_json")
                    or raw_delta.get("arguments")
                    or raw_delta.get("text")
                    or ""
                )
            else:
                delta = str(raw_delta or "")
            if not delta and isinstance(ev.get("partial_json"), str):
                delta = ev["partial_json"]
            if delta:
                idx = self._idx_for(item_id=item_id, call_id=call_id)
                self._calls[idx]["arguments"] += delta
            return out

        if et in (
            "response.function_call_arguments.done",
            "response.custom_tool_call_input.done",
        ):
            item_id = str(ev.get("item_id") or "")
            call_id = str(ev.get("call_id") or "")
            args = ev.get("arguments")
            if isinstance(args, dict):
                try:
                    args = json.dumps(args, ensure_ascii=False)
                except Exception:
                    args = ""
            if isinstance(args, str) and args.strip():
                idx = self._idx_for(item_id=item_id, call_id=call_id)
                self._calls[idx]["arguments"] = args  # done = 权威完整串
            return out

        if et in ("response.completed", "response.done"):
            if self._finished:
                return out
            self._finished = True
            resp = ev.get("response") if isinstance(ev.get("response"), dict) else {}
            if resp:
                if resp.get("id"):
                    self.resp_id = str(resp["id"])
                self._merge_from_final_output(resp)
                if isinstance(resp.get("usage"), dict):
                    self._usage = dict(resp["usage"])
            # some gateways put usage on the event root
            if not self._usage and isinstance(ev.get("usage"), dict):
                self._usage = dict(ev["usage"])
            out.extend(self._emit_tools_and_finish())
            return out

        if et and "delta" in et and isinstance(ev.get("delta"), str) and ev["delta"]:
            out.append(
                _sse_chat_chunk(
                    model=self.model, content=str(ev["delta"]), chunk_id=self.resp_id
                )
            )
        return out

    def close(self) -> list[str]:
        if self._finished:
            return []
        self._finished = True
        return self._emit_tools_and_finish()


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
                    # 聚合 SSE → 完整 chat.completion（含 tool_calls）
                    content_parts: list[str] = []
                    usage: dict[str, Any] = {}
                    resp_id = "codex-oauth"
                    tool_acc: dict[int, dict[str, str]] = {}
                    next_idx = 0
                    id_map: dict[str, int] = {}

                    def _idx(call_id: str = "", item_id: str = "", name: str = "") -> int:
                        nonlocal next_idx
                        for k in (call_id, item_id):
                            if k and k in id_map:
                                return id_map[k]
                        i = next_idx
                        next_idx += 1
                        if call_id:
                            id_map[call_id] = i
                        if item_id:
                            id_map[item_id] = i
                        tool_acc[i] = {
                            "id": call_id or item_id or f"call_{i}",
                            "name": name,
                            "arguments": "",
                        }
                        return i

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
                        et = str(ev.get("type") or "")
                        if isinstance(ev.get("response"), dict):
                            r = ev["response"]
                            if r.get("id"):
                                resp_id = str(r["id"])
                            if isinstance(r.get("usage"), dict):
                                usage = r["usage"]
                            if et in ("response.completed", "response.done"):
                                t = _extract_text_from_codex(r)
                                if t and not content_parts:
                                    content_parts.append(t)
                                for tc in _extract_tool_calls_from_codex(r):
                                    fn = tc.get("function") or {}
                                    i = _idx(
                                        call_id=str(tc.get("id") or ""),
                                        name=str(fn.get("name") or ""),
                                    )
                                    tool_acc[i]["name"] = str(fn.get("name") or "")
                                    tool_acc[i]["arguments"] = str(
                                        fn.get("arguments") or "{}"
                                    )
                                    tool_acc[i]["id"] = str(tc.get("id") or tool_acc[i]["id"])
                        if et in (
                            "response.output_text.delta",
                            "response.output_text.delta.event",
                        ):
                            content_parts.append(str(ev.get("delta") or ""))
                        elif et in (
                            "response.output_item.added",
                            "response.output_item.done",
                        ):
                            item = ev.get("item") if isinstance(ev.get("item"), dict) else {}
                            if item.get("type") in ("function_call", "custom_tool_call"):
                                i = _idx(
                                    call_id=str(item.get("call_id") or ""),
                                    item_id=str(item.get("id") or ""),
                                    name=str(item.get("name") or ""),
                                )
                                if item.get("name"):
                                    tool_acc[i]["name"] = str(item["name"])
                                args = item.get("arguments")
                                if isinstance(args, str) and args:
                                    tool_acc[i]["arguments"] = args
                        elif et in (
                            "response.function_call_arguments.delta",
                            "response.custom_tool_call_input.delta",
                        ):
                            i = _idx(
                                call_id=str(ev.get("call_id") or ""),
                                item_id=str(ev.get("item_id") or ""),
                            )
                            tool_acc[i]["arguments"] += str(ev.get("delta") or "")
                        elif et in (
                            "response.function_call_arguments.done",
                            "response.custom_tool_call_input.done",
                        ):
                            i = _idx(
                                call_id=str(ev.get("call_id") or ""),
                                item_id=str(ev.get("item_id") or ""),
                            )
                            if isinstance(ev.get("arguments"), str) and ev["arguments"]:
                                if not tool_acc[i]["arguments"]:
                                    tool_acc[i]["arguments"] = ev["arguments"]

                    content = "".join(content_parts)
                    tool_calls = []
                    for i in sorted(tool_acc.keys()):
                        e = tool_acc[i]
                        if not (e.get("name") or "").strip():
                            continue
                        tool_calls.append(
                            {
                                "id": e["id"],
                                "type": "function",
                                "function": {
                                    "name": e["name"],
                                    "arguments": e.get("arguments") or "{}",
                                },
                            }
                        )
                    msg: dict[str, Any] = {
                        "role": "assistant",
                        "content": content or (None if tool_calls else ""),
                    }
                    if tool_calls:
                        msg["tool_calls"] = tool_calls
                    return {
                        "id": resp_id,
                        "object": "chat.completion",
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "message": msg,
                                "finish_reason": "tool_calls" if tool_calls else "stop",
                            }
                        ],
                        "usage": _map_responses_usage(usage) if usage else {},
                    }
        except Exception as e:
            logger.warning("openai-codex proxy failed: %s", e)
            return JSONResponse(
                {"error": {"message": str(e), "type": "proxy_error"}},
                status_code=502,
            )

    async def _gen():
        conv = _CodexStreamToChat(model)
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
                        yield _sse_chat_chunk(
                            model=model,
                            content=(
                                f"[Codex OAuth error {resp.status}] {err[:400]}"
                            ),
                            finish_reason="stop",
                            chunk_id="codex-err",
                        )
                        yield "data: [DONE]\n\n"
                        return
                    async for raw in resp.content:
                        line = raw.decode("utf-8", errors="replace").strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data_s = line[5:].strip()
                        if data_s == "[DONE]":
                            for part in conv.close():
                                yield part
                            break
                        try:
                            ev = json.loads(data_s)
                        except Exception:
                            continue
                        if not isinstance(ev, dict):
                            continue
                        for part in conv.feed(ev):
                            yield part
                    else:
                        for part in conv.close():
                            yield part
        except Exception as e:
            yield _sse_chat_chunk(
                model=model,
                content=f"[proxy stream error] {e}",
                finish_reason="stop",
                chunk_id="codex-err",
            )
            yield "data: [DONE]\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")
