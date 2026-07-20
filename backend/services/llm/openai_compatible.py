"""
通用 OpenAI 兼容 LLM 服务实现
支持 vLLM、TGI、llama.cpp server、LM Studio、Text Generation Inference 等
任何遵循 OpenAI /v1/chat/completions 格式的本地或远程服务
"""

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from backend.core.config import settings

from .interface import LLMService
from .schemas import LLMChunk, LLMResponse, ToolCall

logger = logging.getLogger(__name__)


class OpenAICompatibleService(LLMService):
    """通用 OpenAI 兼容 LLM 服务"""

    def __init__(self, config=None):
        self.config = config or settings.get_llm_config()
        base = (self.config.base_url or "").strip().strip("\"'")
        self.base_url = base.rstrip("/")
        self.model = self._normalize_model_id(
            getattr(self.config, "model", "") or "",
            self.base_url,
        )
        from .param_sanitize import sanitize_max_tokens, sanitize_temperature
        self.max_tokens = sanitize_max_tokens(
            getattr(self.config, "max_tokens", None), model=self.model
        )
        self.temperature = sanitize_temperature(getattr(self.config, "temperature", 0.7))
        self.api_key = getattr(self.config, "api_key", None)

    @staticmethod
    def _normalize_model_id(model: str, base_url: str) -> str:
        """Kimi Code 仅接受 kimi-for-coding / kimi-for-coding-highspeed。"""
        m = (model or "").strip()
        b = (base_url or "").lower()
        if "kimi.com/coding" in b or "api.kimi.com/coding" in b:
            aliases = {
                "k3": "kimi-for-coding",
                "kimi-k3": "kimi-for-coding",
                "kimi_k3": "kimi-for-coding",
                "k3-highspeed": "kimi-for-coding-highspeed",
                "k3_highspeed": "kimi-for-coding-highspeed",
                "k3-hs": "kimi-for-coding-highspeed",
            }
            key = m.lower()
            if key in aliases:
                fixed = aliases[key]
                logger.warning("Kimi Code model id %r mapped to %r", m, fixed)
                return fixed
            if m and m not in ("kimi-for-coding", "kimi-for-coding-highspeed"):
                logger.warning("Kimi Code unexpected model %r; use kimi-for-coding", m)
                return "kimi-for-coding"
        return m

    def _get_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _chat_completions_url(self) -> str:
        """兼容 base_url 已含版本号（/v1 /v2 /v4 /api 等）的写法，避免拼出 /v1/v1/... 或 /v2/v1/..."""
        import re as _re
        base = self.base_url.rstrip("/")
        if _re.search(r"/(v\d+|api)$", base):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def _normalize_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized = []
        for t in tools:
            if t.get("type") == "function" and "function" in t:
                normalized.append(t)
            else:
                normalized.append({"type": "function", "function": t})
        return normalized

    # 部分兼容网关（讯飞 MaaS 等）拒绝：assistant 空字符串 content + tool_calls
    # 以及超大历史 tool arguments。统一在出站前消毒。
    _MAX_TOOL_ARG_CHARS = 6000
    _MAX_TOOL_RESULT_CHARS = 12000

    @classmethod
    def _sanitize_messages_for_api(cls, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize message list for strict OpenAI-compatible gateways.

        Critical: function.arguments MUST remain valid JSON after any truncation
        (iFlytek MaaS returns 400 otherwise).
        """
        out: list[dict[str, Any]] = []
        pending_tool_ids: set[str] = set()

        def _trim_text(s: str, limit: int) -> str:
            if len(s) <= limit:
                return s
            return s[:limit] + f"\n...[truncated {len(s) - limit} chars]"

        def _safe_tool_arguments(args: Any) -> str:
            """Return a JSON string always parseable, length-capped."""
            raw: str
            parsed: Any = None
            if isinstance(args, str):
                raw = args
                try:
                    parsed = json.loads(args) if args.strip() else {}
                except Exception:
                    parsed = None
            elif args is None:
                return "{}"
            else:
                try:
                    raw = json.dumps(args, ensure_ascii=False)
                    parsed = args
                except Exception:
                    raw = json.dumps({"value": str(args)}, ensure_ascii=False)
                    parsed = {"value": str(args)}

            if len(raw) <= cls._MAX_TOOL_ARG_CHARS and parsed is not None:
                # re-dump to guarantee compact valid JSON
                try:
                    return json.dumps(parsed, ensure_ascii=False)
                except Exception:
                    return raw if len(raw) <= cls._MAX_TOOL_ARG_CHARS else json.dumps(
                        {"_truncated": True, "preview": raw[:800]}, ensure_ascii=False
                    )

            # Too long or invalid JSON string → structured stub (still valid JSON)
            preview = raw[:800] if isinstance(raw, str) else str(raw)[:800]
            stub: dict[str, Any] = {
                "_truncated": True,
                "original_chars": len(raw) if isinstance(raw, str) else 0,
                "preview": preview,
            }
            # Keep top-level keys from object if possible (names only)
            if isinstance(parsed, dict):
                keys = list(parsed.keys())[:20]
                stub["keys"] = keys
                # preserve small scalar fields
                small = {}
                for k, v in parsed.items():
                    if isinstance(v, (int, float, bool)) or (isinstance(v, str) and len(v) <= 200):
                        small[k] = v
                    if len(small) >= 8:
                        break
                if small:
                    stub["fields"] = small
            return json.dumps(stub, ensure_ascii=False)

        for raw_msg in messages:
            if not isinstance(raw_msg, dict):
                continue
            m = dict(raw_msg)
            role = m.get("role")

            if role == "assistant":
                tcs = m.get("tool_calls")
                content = m.get("content")
                if tcs:
                    if content is None or (isinstance(content, str) and not content.strip()):
                        m["content"] = None
                    new_tcs: list[dict[str, Any]] = []
                    if isinstance(tcs, list):
                        for tc in tcs:
                            if not isinstance(tc, dict):
                                continue
                            tc2 = dict(tc)
                            fn = dict(tc2.get("function") or {})
                            fn["arguments"] = _safe_tool_arguments(fn.get("arguments"))
                            # name required
                            if not (fn.get("name") or "").strip():
                                fn["name"] = fn.get("name") or "unknown_tool"
                            tc2["function"] = fn
                            if not tc2.get("type"):
                                tc2["type"] = "function"
                            tid = tc2.get("id")
                            if tid:
                                pending_tool_ids.add(str(tid))
                            new_tcs.append(tc2)
                    m["tool_calls"] = new_tcs
                else:
                    if content is None:
                        m["content"] = ""
                    m.pop("tool_calls", None)
                out.append(m)
                continue

            if role == "tool":
                content = m.get("content")
                if content is None:
                    m["content"] = ""
                elif not isinstance(content, str):
                    m["content"] = str(content)
                if len(m["content"]) > cls._MAX_TOOL_RESULT_CHARS:
                    m["content"] = _trim_text(m["content"], cls._MAX_TOOL_RESULT_CHARS)
                tid = m.get("tool_call_id")
                if tid:
                    pending_tool_ids.discard(str(tid))
                elif pending_tool_ids:
                    m["tool_call_id"] = next(iter(pending_tool_ids))
                    pending_tool_ids.discard(m["tool_call_id"])
                out.append(m)
                continue

            if role in ("user", "system"):
                if m.get("content") is None:
                    m["content"] = ""
                out.append(m)
                continue

            out.append(m)

        if pending_tool_ids:
            for tid in list(pending_tool_ids):
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": tid,
                        "content": "[tool result missing — interrupted]",
                    }
                )
                pending_tool_ids.discard(tid)

        return out

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = True,
    ) -> AsyncIterator[LLMChunk]:
        """调用 OpenAI 兼容 /v1/chat/completions，支持流式和非流式"""
        url = self._chat_completions_url()
        safe_messages = self._sanitize_messages_for_api(messages)
        payload = {
            "model": self.model,
            "messages": safe_messages,
            "stream": stream,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if tools:
            payload["tools"] = self._normalize_tools(tools)
            payload["tool_choice"] = "auto"

        message_id = uuid.uuid4()

        if not stream:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload, headers=self._get_headers()) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
                        choice = data.get("choices", [{}])[0]
                        msg = choice.get("message", {})
                        content = msg.get("content", "") or ""
                        tool_calls = msg.get("tool_calls", [])
                        finish_reason = choice.get("finish_reason", "stop")
                        reasoning = (
                            msg.get("reasoning_content")
                            or msg.get("reasoning")
                            or ""
                        )
                        if isinstance(reasoning, dict):
                            reasoning = reasoning.get("text") or reasoning.get("content") or ""

                        if tool_calls:
                            for tc in tool_calls:
                                try:
                                    raw_args = (tc.get("function") or {}).get("arguments")
                                    if isinstance(raw_args, str):
                                        args = json.loads(raw_args)
                                    else:
                                        args = raw_args or {}
                                except (json.JSONDecodeError, KeyError, TypeError):
                                    args = {}
                                if not isinstance(args, dict):
                                    args = {"value": args}
                                yield LLMChunk(
                                    message_id=message_id,
                                    delta="",
                                    tool_call=ToolCall(
                                        id=tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                                        name=(tc.get("function") or {}).get("name", ""),
                                        arguments=args,
                                    ),
                                )
                        if reasoning:
                            yield LLMChunk(
                                message_id=message_id,
                                delta="",
                                reasoning_delta=str(reasoning),
                            )
                        yield LLMChunk(
                            message_id=message_id,
                            delta=content or "",
                            finish_reason=finish_reason,
                        )
            except aiohttp.ClientResponseError as e:
                logger.error(f"OpenAI-compatible chat error: status={e.status}, message='{e.message}', url='{e.request_info.url}'")

                body = ""
                try:
                    body = await e.response.text()
                    logger.error(f"Response body: {body[:2000]}")
                except Exception:
                    pass
                if getattr(e, "status", None) in (429, 500, 502, 503, 504):
                    raise
                detail = (body or getattr(e, "message", "") or str(e)).strip()
                if len(detail) > 800:
                    detail = detail[:800] + "…"
                hint = ""
                url = str(getattr(getattr(e, "request_info", None), "url", "") or "")
                status = getattr(e, "status", "error")
                if status == 400 and "kimi.com/coding" in url:
                    hint = (
                        " Kimi Code model 须为 kimi-for-coding / kimi-for-coding-highspeed"
                        f"（当前 model={self.model!r}）。"
                    )
                elif status in (401, 403):
                    hint = (
                        " 鉴权失败：请到「设置 → 模型」检查 API Key / OAuth 是否有效、"
                        f"供应商 base_url 是否匹配（当前 model={self.model!r}）。"
                    )
                elif status == 400:
                    hint = (
                        " 请求被拒：常见原因是 model 名错误、上下文过长、或工具 schema 不兼容。"
                        f"（当前 model={self.model!r}）"
                    )
                yield LLMChunk(
                    message_id=message_id,
                    delta=f"[LLM Error {status}] {detail}{hint}",
                    finish_reason="error",
                )
            except Exception as e:
                logger.error(f"OpenAI-compatible stream error: {e}")
                yield LLMChunk(message_id=message_id, delta=f"[LLM Error] {e}", finish_reason="error")
            return

        accumulated_tool_calls: dict[int, dict[str, Any]] = {}
        last_finish_reason: str | None = None

        def _merge_tool_delta(tc: dict[str, Any]) -> None:
            """合并流式 tool_call 增量（后续 chunk 可能补全 id/name）。"""
            index = tc.get("index", 0)
            fn = tc.get("function") or {}
            if index not in accumulated_tool_calls:
                accumulated_tool_calls[index] = {
                    "id": tc.get("id") or "",
                    "name": fn.get("name") or "",
                    "arguments": fn.get("arguments") or "",
                }
                return
            entry = accumulated_tool_calls[index]
            if tc.get("id"):
                entry["id"] = tc["id"]
            if fn.get("name"):
                entry["name"] = fn["name"]
            if fn.get("arguments"):
                entry["arguments"] = (entry.get("arguments") or "") + fn["arguments"]

        def _emit_tool_calls() -> list[LLMChunk]:
            """无论 finish_reason 是 tool_calls 还是 stop，只要有工具调用就发出。"""
            out: list[LLMChunk] = []
            for tc_data in accumulated_tool_calls.values():
                name = (tc_data.get("name") or "").strip()
                if not name:
                    logger.warning("Skipping stream tool_call with empty name: %s", tc_data)
                    continue
                try:
                    args = json.loads(tc_data.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                if not isinstance(args, dict):
                    args = {"value": args}
                out.append(
                    LLMChunk(
                        message_id=message_id,
                        delta="",
                        tool_call=ToolCall(
                            id=tc_data.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                            name=name,
                            arguments=args,
                        ),
                    )
                )
            return out

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, headers=self._get_headers()
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.content:
                        line = line.decode("utf-8").strip()
                        if not line or line == "data: [DONE]":
                            continue
                        if not line.startswith("data: "):
                            continue

                        try:
                            data = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue

                        choice = data.get("choices", [{}])[0]
                        delta = choice.get("delta", {})

                        content = delta.get("content", "") or ""
                        if content:
                            yield LLMChunk(message_id=message_id, delta=content)

                        # 思考链：DeepSeek/Qwen/部分兼容接口
                        reasoning = (
                            delta.get("reasoning_content")
                            or delta.get("reasoning")
                            or delta.get("thought")
                            or ""
                        )
                        if isinstance(reasoning, dict):
                            reasoning = (
                                reasoning.get("text")
                                or reasoning.get("content")
                                or reasoning.get("summary")
                                or ""
                            )
                        if reasoning:
                            yield LLMChunk(
                                message_id=message_id,
                                delta="",
                                reasoning_delta=str(reasoning),
                            )

                        for tc in delta.get("tool_calls") or []:
                            _merge_tool_delta(tc)

                        finish_reason = choice.get("finish_reason")
                        if finish_reason:
                            last_finish_reason = finish_reason
                            # 关键：部分兼容服务商用 stop / function_call 结束，但仍带 tool_calls
                            emitted = _emit_tool_calls()
                            for chunk in emitted:
                                yield chunk
                            effective = "tool_calls" if emitted else finish_reason
                            yield LLMChunk(
                                message_id=message_id, delta="", finish_reason=effective
                            )
                            break
                    else:
                        # 流正常结束但没有 finish_reason：仍冲刷工具调用
                        if accumulated_tool_calls:
                            for chunk in _emit_tool_calls():
                                yield chunk
                            yield LLMChunk(
                                message_id=message_id,
                                delta="",
                                finish_reason="tool_calls",
                            )
                        elif last_finish_reason is None:
                            yield LLMChunk(
                                message_id=message_id, delta="", finish_reason="stop"
                            )

        except aiohttp.ClientResponseError as e:
            logger.error(f"OpenAI-compatible chat error: status={e.status}, message='{e.message}', url='{e.request_info.url}'")
            body = ""
            try:
                body = await e.response.text()
                logger.error(f"Response body: {body[:2000]}")
            except Exception:
                pass
            if e.status in (429, 500, 502, 503, 504):
                raise
            detail = (body or e.message or "").strip()
            if len(detail) > 800:
                detail = detail[:800] + "…"
            hint = ""
            url = str(getattr(getattr(e, "request_info", None), "url", "") or "")
            if e.status == 400 and "kimi.com/coding" in url:
                hint = (
                    " Kimi Code model 须为 kimi-for-coding / kimi-for-coding-highspeed"
                    f"（当前 model={self.model!r}）。"
                )
            elif e.status in (401, 403):
                hint = (
                    " 鉴权失败：请到「设置 → 模型」检查 API Key / OAuth 是否有效、"
                    f"供应商 base_url 是否匹配（当前 model={self.model!r}）。"
                )
            elif e.status == 400:
                hint = (
                    " 请求被拒：常见原因是 model 名错误、上下文过长、或工具 schema 不兼容。"
                    f"（当前 model={self.model!r}）"
                )
            yield LLMChunk(
                message_id=message_id,
                delta=f"[LLM Error {e.status}] {detail or e.message}{hint}",
                finish_reason="error",
            )
        except Exception as e:
            logger.error(f"OpenAI-compatible chat error: {e}")
            name = type(e).__name__
            if name in (
                "ClientConnectorError",
                "ServerTimeoutError",
                "ClientOSError",
                "ClientPayloadError",
                "TimeoutError",
            ) or "timeout" in str(e).lower() or "connect" in str(e).lower():
                raise
            yield LLMChunk(message_id=message_id, delta=f"[LLM Error] {e}", finish_reason="error")

    async def chat_complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """非流式调用 OpenAI 兼容服务"""
        chunks = []
        async for chunk in self.chat(messages, tools, stream=False):
            chunks.append(chunk)

        content = "".join(c.delta for c in chunks)
        tool_calls = [c.tool_call for c in chunks if c.tool_call is not None]
        finish_reason = chunks[-1].finish_reason if chunks else "stop"

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )
