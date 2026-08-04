"""
Ollama LLM 服务实现
对接 Ollama /api/chat 端点，支持 tools + stream
"""

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from backend.core.config import settings

from .http_session import ensure_session, request_timeout, stream_timeout
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
                session = ensure_session(self)
                async with session.post(url, json=payload, timeout=request_timeout()) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    msg = data.get("message") if isinstance(data.get("message"), dict) else {}
                    content = (msg or {}).get("content", "") or ""
                    # 与 stream 路径一致：非流式也必须吐出 tool_calls，否则 Ollama
                    # 供应商在 stream=False 时永远无法闭环工具（其它 OpenAI 兼容族已支持）。
                    raw_tcs = (msg or {}).get("tool_calls") or []
                    if isinstance(raw_tcs, list):
                        for tc in raw_tcs:
                            if not isinstance(tc, dict):
                                continue
                            func = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                            if not func:
                                continue
                            args = func.get("arguments", {})
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args) if args.strip() else {}
                                except Exception:
                                    args = {"raw": args}
                            if not isinstance(args, dict):
                                args = {"value": args}
                            yield LLMChunk(
                                message_id=message_id,
                                delta="",
                                tool_call=ToolCall(
                                    id=str(tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"),
                                    name=str(func.get("name") or ""),
                                    arguments=args,
                                ),
                            )
                    finish = "tool_calls" if raw_tcs else "stop"
                    if content or not raw_tcs:
                        yield LLMChunk(
                            message_id=message_id,
                            delta=content,
                            finish_reason=finish,
                        )
                    elif raw_tcs:
                        yield LLMChunk(
                            message_id=message_id, delta="", finish_reason="tool_calls"
                        )
            except Exception as e:
                logger.error(f"Ollama chat error: {e}")
                yield LLMChunk(message_id=message_id, delta="", finish_reason="error")
            return

        accumulated_content = ""

        try:
            session = ensure_session(self)
            async with session.post(url, json=payload, timeout=stream_timeout()) as resp:
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
