"""
vLLM (OpenAI 兼容) LLM 服务实现
对接 vLLM /v1/chat/completions 端点，支持 tools + stream
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


class VLLMService(LLMService):
    """vLLM (OpenAI 兼容) LLM 服务"""

    def __init__(self, config=None):
        self.config = config or settings.get_llm_config()
        self.base_url = self.config.base_url.rstrip("/")
        self.model = self.config.model
        from .param_sanitize import sanitize_max_tokens, sanitize_temperature
        self.max_tokens = sanitize_max_tokens(
            getattr(self.config, "max_tokens", None), model=self.model
        )
        self.temperature = sanitize_temperature(getattr(self.config, "temperature", 0.7))
        self.api_key = getattr(self.config, "api_key", None)

    def _get_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _normalize_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized = []
        for t in tools:
            if t.get("type") == "function" and "function" in t:
                normalized.append(t)
            else:
                normalized.append({"type": "function", "function": t})
        return normalized

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = True,
    ) -> AsyncIterator[LLMChunk]:
        """调用 vLLM /v1/chat/completions，支持流式和非流式"""
        url = f"{self.base_url}/v1/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
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
                        content = msg.get("content", "")
                        tool_calls = msg.get("tool_calls", [])
                        finish_reason = choice.get("finish_reason", "stop")

                        if tool_calls:
                            for tc in tool_calls:
                                try:
                                    args = json.loads(tc["function"]["arguments"]) if isinstance(tc["function"]["arguments"], str) else tc["function"]["arguments"]
                                except (json.JSONDecodeError, KeyError):
                                    args = {}
                                yield LLMChunk(
                                    message_id=message_id,
                                    delta="",
                                    tool_call=ToolCall(
                                        id=tc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                                        name=tc.get("function", {}).get("name", ""),
                                        arguments=args,
                                    ),
                                )
                        yield LLMChunk(message_id=message_id, delta=content, finish_reason=finish_reason)
            except Exception as e:
                logger.error(f"vLLM chat error: {e}")
                yield LLMChunk(message_id=message_id, delta="", finish_reason="error")
            return

        accumulated_tool_calls: dict[int, dict[str, Any]] = {}

        def _merge_tool_delta(tc: dict[str, Any]) -> None:
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
            out: list[LLMChunk] = []
            for tc_data in accumulated_tool_calls.values():
                name = (tc_data.get("name") or "").strip()
                if not name:
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

                        content = delta.get("content", "")
                        if content:
                            yield LLMChunk(message_id=message_id, delta=content)

                        for tc in delta.get("tool_calls") or []:
                            _merge_tool_delta(tc)

                        finish_reason = choice.get("finish_reason")
                        if finish_reason:
                            emitted = _emit_tool_calls()
                            for chunk in emitted:
                                yield chunk
                            effective = "tool_calls" if emitted else finish_reason
                            yield LLMChunk(
                                message_id=message_id, delta="", finish_reason=effective
                            )
                            break
                    else:
                        if accumulated_tool_calls:
                            for chunk in _emit_tool_calls():
                                yield chunk
                            yield LLMChunk(
                                message_id=message_id, delta="", finish_reason="tool_calls"
                            )

        except Exception as e:
            logger.error(f"vLLM chat error: {e}")
            yield LLMChunk(message_id=message_id, delta="", finish_reason="error")

    async def chat_complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """非流式调用 vLLM"""
        chunks = []
        async for chunk in self.chat(messages, tools, stream=False):
            chunks.append(chunk)

        content = "".join(c.delta for c in chunks)
        tool_calls = [
            c.tool_call for c in chunks if c.tool_call is not None
        ]
        finish_reason = chunks[-1].finish_reason if chunks else "stop"

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )
