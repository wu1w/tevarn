"""
Anthropic Claude LLM 服务实现
对接 Anthropic Messages API (https://api.anthropic.com/v1/messages)
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


class AnthropicService(LLMService):
    """Anthropic Claude LLM 服务"""

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
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def _convert_messages(self, messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
        """将 OpenAI 格式消息转换为 Anthropic 格式

        Anthropic 使用 system 为独立字段，messages 中不能有 role=system。
        助手 tool_calls → content 中的 tool_use 块；
        role=tool → user 消息里的 tool_result（必须带 tool_use_id）。
        """
        system_parts: list[str] = []
        anthropic_messages: list[dict[str, Any]] = []

        def _flush_tool_results(pending: list[dict[str, Any]]) -> None:
            if not pending:
                return
            # 合并连续 tool_result 为一条 user 消息，符合 Anthropic 交替角色要求
            anthropic_messages.append({"role": "user", "content": pending[:]})
            pending.clear()

        pending_tool_results: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content") or ""

            if role == "system":
                if content:
                    system_parts.append(str(content))
                continue

            if role == "tool":
                tool_use_id = (
                    msg.get("tool_call_id")
                    or msg.get("id")
                    or f"toolu_{uuid.uuid4().hex[:12]}"
                )
                pending_tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": str(tool_use_id),
                        "content": str(content),
                    }
                )
                continue

            _flush_tool_results(pending_tool_results)

            if role == "assistant":
                blocks: list[dict[str, Any]] = []
                if content:
                    blocks.append({"type": "text", "text": str(content)})
                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    name = fn.get("name") or tc.get("name") or ""
                    raw_args = fn.get("arguments", tc.get("arguments", {}))
                    if isinstance(raw_args, str):
                        try:
                            args = json.loads(raw_args or "{}")
                        except json.JSONDecodeError:
                            args = {}
                    elif isinstance(raw_args, dict):
                        args = raw_args
                    else:
                        args = {}
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:12]}",
                            "name": name,
                            "input": args if isinstance(args, dict) else {"value": args},
                        }
                    )
                if not blocks:
                    blocks = [{"type": "text", "text": ""}]
                anthropic_messages.append({"role": "assistant", "content": blocks})
                continue

            # user / 其他
            anthropic_messages.append({"role": "user" if role == "user" else role, "content": content})

        _flush_tool_results(pending_tool_results)

        system_text = "\n\n".join(system_parts) if system_parts else None
        return system_text, anthropic_messages

    def _convert_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """将 OpenAI 格式工具定义转换为 Anthropic 格式"""
        anthropic_tools = []
        for t in tools:
            func = t.get("function", t)
            anthropic_tools.append({
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {}),
            })
        return anthropic_tools

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = True,
    ) -> AsyncIterator[LLMChunk]:
        """调用 Anthropic /v1/messages，支持流式和非流式"""
        url = f"{self.base_url}/v1/messages"
        system_text, anthropic_messages = self._convert_messages(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": anthropic_messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": stream,
        }
        if system_text:
            payload["system"] = system_text
        if tools:
            payload["tools"] = self._convert_tools(tools)

        message_id = uuid.uuid4()

        if not stream:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload, headers=self._get_headers()) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
                        content_parts = []
                        tool_calls = []
                        for block in data.get("content", []):
                            if block.get("type") == "text":
                                content_parts.append(block.get("text", ""))
                            elif block.get("type") == "tool_use":
                                tc = ToolCall(
                                    id=block.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                                    name=block.get("name", ""),
                                    arguments=block.get("input", {}),
                                )
                                tool_calls.append(tc)
                                yield LLMChunk(message_id=message_id, delta="", tool_call=tc)
                        finish_reason = "tool_calls" if tool_calls else data.get("stop_reason", "stop")
                        yield LLMChunk(message_id=message_id, delta="".join(content_parts), finish_reason=finish_reason)
            except Exception as e:
                logger.error(f"Anthropic chat error: {e}")
                yield LLMChunk(message_id=message_id, delta="", finish_reason="error")
            return

        accumulated_content = ""
        current_tool_call: dict[str, Any] | None = None
        tool_calls_list: list[ToolCall] = []

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=payload, headers=self._get_headers()
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.content:
                        line = line.decode("utf-8").strip()
                        if not line or not line.startswith("data: "):
                            continue
                        if line == "data: [DONE]":
                            continue

                        try:
                            data = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue

                        event_type = data.get("type", "")

                        if event_type == "content_block_delta":
                            delta = data.get("delta", {})
                            delta_type = delta.get("type", "")

                            if delta_type == "text_delta":
                                text = delta.get("text", "")
                                if text:
                                    accumulated_content += text
                                    yield LLMChunk(message_id=message_id, delta=text)

                            elif delta_type == "input_json_delta":
                                partial_json = delta.get("partial_json", "")
                                if current_tool_call is not None and partial_json:
                                    current_tool_call["arguments_json"] = current_tool_call.get("arguments_json", "") + partial_json

                        elif event_type == "content_block_start":
                            content_block = data.get("content_block", {})
                            block_type = content_block.get("type", "")
                            if block_type == "tool_use":
                                current_tool_call = {
                                    "id": content_block.get("id", ""),
                                    "name": content_block.get("name", ""),
                                    "arguments_json": "",
                                }

                        elif event_type == "content_block_stop":
                            if current_tool_call is not None:
                                try:
                                    args = json.loads(current_tool_call.get("arguments_json", "{}"))
                                except json.JSONDecodeError:
                                    args = {}
                                tc = ToolCall(
                                    id=current_tool_call.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                                    name=current_tool_call.get("name", ""),
                                    arguments=args,
                                )
                                tool_calls_list.append(tc)
                                yield LLMChunk(message_id=message_id, delta="", tool_call=tc)
                                current_tool_call = None

                        elif event_type == "message_stop":
                            finish_reason = "tool_calls" if tool_calls_list else "stop"
                            yield LLMChunk(message_id=message_id, delta="", finish_reason=finish_reason)
                            break

        except Exception as e:
            logger.error(f"Anthropic chat error: {e}")
            yield LLMChunk(message_id=message_id, delta="", finish_reason="error")

    async def chat_complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """非流式调用 Anthropic"""
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
