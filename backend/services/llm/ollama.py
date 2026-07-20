"""
Ollama LLM 服务实现
对接 Ollama /api/chat 端点，支持 tools + stream
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


class OllamaService(LLMService):
    """Ollama LLM 服务"""

    def __init__(self, config=None):
        self.config = config or settings.get_llm_config()
        self.base_url = self.config.base_url.rstrip("/")
        self.model = self.config.model
        from .param_sanitize import sanitize_max_tokens, sanitize_temperature
        self.max_tokens = sanitize_max_tokens(
            getattr(self.config, "max_tokens", None), model=self.model
        )
        self.temperature = sanitize_temperature(getattr(self.config, "temperature", 0.7))

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = True,
    ) -> AsyncIterator[LLMChunk]:
        """调用 Ollama /api/chat，支持流式和非流式"""
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "options": {
                "num_predict": self.max_tokens,
                "temperature": self.temperature,
            },
        }
        if tools:
            payload["tools"] = tools

        message_id = uuid.uuid4()

        if not stream:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload) as resp:
                        resp.raise_for_status()
                        data = await resp.json()
                        content = data.get("message", {}).get("content", "")
                        yield LLMChunk(message_id=message_id, delta=content, finish_reason="stop")
            except Exception as e:
                logger.error(f"Ollama chat error: {e}")
                yield LLMChunk(message_id=message_id, delta="", finish_reason="error")
            return

        accumulated_content = ""

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.content:
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        delta = data.get("message", {}).get("content", "")
                        if delta:
                            accumulated_content += delta
                            yield LLMChunk(message_id=message_id, delta=delta)

                        tool_calls = data.get("message", {}).get("tool_calls", [])
                        if tool_calls:
                            for tc in tool_calls:
                                func = tc.get("function", {})
                                if func:
                                    yield LLMChunk(
                                        message_id=message_id,
                                        delta="",
                                        tool_call=ToolCall(
                                            id=f"call_{uuid.uuid4().hex[:8]}",
                                            name=func.get("name", ""),
                                            arguments=func.get("arguments", {}),
                                        ),
                                    )

                        if data.get("done"):
                            yield LLMChunk(message_id=message_id, delta="", finish_reason="stop")
                            break

        except Exception as e:
            logger.error(f"Ollama chat error: {e}")
            yield LLMChunk(message_id=message_id, delta="", finish_reason="error")

    async def chat_complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """非流式调用 Ollama"""
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
