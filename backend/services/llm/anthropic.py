"""
Anthropic Claude LLM 服务实现
对接 Anthropic Messages API (https://api.anthropic.com/v1/messages)
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


def _extract_usage(raw: dict[str, Any]) -> dict[str, int]:
    """Anthropic usage → 统一字段（T4 + billable）。"""
    from .usage_normalize import normalize_usage

    return normalize_usage(raw if isinstance(raw, dict) else {}, family="anthropic")




class AnthropicService(LLMService):
    """Anthropic Claude LLM 服务"""

    def __init__(self, config=None, *, profile=None, provider_id: str | None = None, **_kw):
        self.config = config or settings.get_llm_config()
        self.base_url = self.config.base_url.rstrip("/")
        self.model = self.config.model
        from .param_sanitize import sanitize_max_tokens, sanitize_temperature
        self.max_tokens = sanitize_max_tokens(
            getattr(self.config, "max_tokens", None), model=self.model
        )
        self.temperature = sanitize_temperature(getattr(self.config, "temperature", 0.7))
        self.api_key = getattr(self.config, "api_key", None)
        self.provider_id = (provider_id or getattr(config, "provider_id", None) or "anthropic").strip()
        if profile is not None:
            self.profile = profile
        else:
            from .provider_profiles import resolve_profile
            self.profile = resolve_profile(
                provider_id=self.provider_id or "anthropic",
                base_url=self.base_url,
                model=self.model,
                llm_provider="anthropic",
            )

    def _get_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key
        # Prompt caching: beta header required on many accounts / older gateways;
        # GA still accepts it. Without this, cache_control may be ignored.
        if self._cache_enabled():
            headers["anthropic-beta"] = "prompt-caching-2024-07-31"
        return headers

    # Injected when compress/history lost the real tool row (Anthropic requires pairing).
    _LOST_TOOL_RESULT = "[Error] result lost after context compress"

    def _convert_messages(self, messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
        """将 OpenAI 格式消息转换为 Anthropic 格式

        Anthropic 使用 system 为独立字段，messages 中不能有 role=system。
        助手 tool_calls → content 中的 tool_use 块；
        role=tool → user 消息里的 tool_result（必须带 tool_use_id）。

        对齐官方：
        - tool_result 可带 is_error
        - 无匹配 tool_use 的孤儿 tool_result 丢弃（假 id 会 400）
        - 仍无 result 的 tool_use → 注入 is_error 占位 result（压缩丢 id 后常见）
        """
        try:
            from backend.agent.tool_result_contract import is_tool_error
        except Exception:  # pragma: no cover
            def is_tool_error(result: str | None) -> bool:  # type: ignore
                t = (result or "").lstrip()
                return t.startswith("[Error]") or t.startswith("[error]")

        system_parts: list[str] = []
        anthropic_messages: list[dict[str, Any]] = []
        # All tool_use ids ever emitted (orphan result filter)
        declared_tool_use_ids: set[str] = set()
        # Current assistant turn: tool_use ids still waiting for a result
        open_tool_use_ids: list[str] = []
        pending_tool_results: list[dict[str, Any]] = []

        def _close_open_tool_round() -> None:
            """Flush real results + inject is_error stubs for unanswered tool_use."""
            results = list(pending_tool_results)
            pending_tool_results.clear()
            if open_tool_use_ids:
                for tid in open_tool_use_ids:
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tid,
                            "content": self._LOST_TOOL_RESULT,
                            "is_error": True,
                        }
                    )
                logger.info(
                    "anthropic convert: injected %d lost tool_result stub(s) after compress",
                    len(open_tool_use_ids),
                )
                open_tool_use_ids.clear()
            if results:
                # One user message with all tool_results (official alternating roles)
                anthropic_messages.append({"role": "user", "content": results})

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content") or ""

            if role == "system":
                if content:
                    system_parts.append(str(content))
                continue

            if role == "tool":
                tool_use_id = str(
                    msg.get("tool_call_id") or msg.get("id") or ""
                ).strip()
                # Orphan result (wrong/missing id): drop — do not invent toolu_*
                if not tool_use_id or tool_use_id not in declared_tool_use_ids:
                    logger.debug(
                        "dropping orphan tool_result (tool_use_id=%r not declared)",
                        tool_use_id or "(empty)",
                    )
                    continue
                block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": str(content),
                }
                if is_tool_error(str(content)):
                    block["is_error"] = True
                pending_tool_results.append(block)
                try:
                    open_tool_use_ids.remove(tool_use_id)
                except ValueError:
                    pass
                continue

            # Any non-tool turn closes the previous tool_use round first
            _close_open_tool_round()

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
                    tid = str(tc.get("id") or "").strip()
                    if not tid:
                        tid = f"toolu_{uuid.uuid4().hex[:12]}"
                    declared_tool_use_ids.add(tid)
                    open_tool_use_ids.append(tid)
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": tid,
                            "name": name,
                            "input": args if isinstance(args, dict) else {"value": args},
                        }
                    )
                if not blocks:
                    blocks = [{"type": "text", "text": ""}]
                anthropic_messages.append({"role": "assistant", "content": blocks})
                continue

            # user / 其他
            anthropic_messages.append(
                {"role": "user" if role == "user" else role, "content": content}
            )

        _close_open_tool_round()

        system_text = "\n\n".join(system_parts) if system_parts else None
        return system_text, anthropic_messages

    def _convert_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """将 OpenAI 格式工具定义转换为 Anthropic 格式。

        Sort by name so the tools prefix is byte-stable across rounds (required
        for cache_control on the last tool to hit).
        """
        anthropic_tools = []
        for t in tools or []:
            func = t.get("function", t) if isinstance(t, dict) else {}
            if not isinstance(func, dict):
                continue
            name = str(func.get("name") or "")
            params = func.get("parameters") or func.get("input_schema") or {}
            if not isinstance(params, dict):
                params = {}
            # Stable key order inside schema
            try:
                params = json.loads(json.dumps(params, sort_keys=True, ensure_ascii=False))
            except Exception:
                pass
            anthropic_tools.append({
                "name": name,
                "description": str(func.get("description") or ""),
                "input_schema": params,
            })
        anthropic_tools.sort(key=lambda x: x.get("name") or "")
        return anthropic_tools

    @staticmethod
    def _cache_enabled() -> bool:
        try:
            return bool(getattr(settings, "agent_prompt_cache_anthropic", True))
        except Exception:
            return True

    @staticmethod
    def _mark_cache_breakpoint(blocks: list[dict[str, Any]]) -> None:
        from .cache_protocol import mark_cache_breakpoint

        mark_cache_breakpoint(blocks)

    def _apply_prompt_cache(
        self,
        payload: dict[str, Any],
        anthropic_messages: list[dict[str, Any]],
    ) -> None:
        """给 system / tools / 历史前缀打缓存断点（T4）。"""
        from .cache_protocol import apply_anthropic_style_cache

        apply_anthropic_style_cache(
            payload,
            anthropic_messages,
            enabled=self._cache_enabled(),
        )

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

        # T4：prompt caching 断点（system / tools / 历史前缀）
        self._apply_prompt_cache(payload, anthropic_messages)
        if self._cache_enabled() and logger.isEnabledFor(logging.DEBUG):
            n_bp = 0
            sys_b = payload.get("system")
            if isinstance(sys_b, list) and sys_b and "cache_control" in (sys_b[-1] or {}):
                n_bp += 1
            tools_b = payload.get("tools") or []
            if tools_b and isinstance(tools_b[-1], dict) and "cache_control" in tools_b[-1]:
                n_bp += 1
            logger.debug(
                "anthropic prompt_cache breakpoints≈%s tools=%s msgs=%s",
                n_bp,
                len(tools_b) if isinstance(tools_b, list) else 0,
                len(anthropic_messages),
            )

        message_id = uuid.uuid4()

        if not stream:
            try:
                session = ensure_session(self)
                async with session.post(url, json=payload, headers=self._get_headers(), timeout=request_timeout()) as resp:
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
                    _usage = _extract_usage(data.get("usage") or {})
                    yield LLMChunk(
                        message_id=message_id,
                        delta="".join(content_parts),
                        finish_reason=finish_reason,
                        usage=_usage,
                    )
            except Exception as e:
                logger.error(f"Anthropic chat error: {e}")
                yield LLMChunk(message_id=message_id, delta="", finish_reason="error")
            return

        accumulated_content = ""
        # Parallel tool_use blocks: track by content_block index (official partial_json path)
        open_tool_blocks: dict[int, dict[str, Any]] = {}
        tool_calls_list: list[ToolCall] = []
        stream_usage: dict[str, int] = {}

        try:
            session = ensure_session(self)
            async with session.post(
                url, json=payload, headers=self._get_headers(),
                timeout=stream_timeout(),
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

                    # 用量与缓存命中（T4）：input/cache_* 在 message_start，
                    # output_tokens 在 message_delta —— 两处都要收。
                    if event_type in ("message_start", "message_delta"):
                        src = (
                            (data.get("message") or {}).get("usage")
                            if event_type == "message_start"
                            else data.get("usage")
                        )
                        if isinstance(src, dict):
                            stream_usage.update(_extract_usage(src))

                    if event_type == "content_block_delta":
                        delta = data.get("delta", {})
                        delta_type = delta.get("type", "")
                        block_index = int(data.get("index", 0) or 0)

                        if delta_type == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                accumulated_content += text
                                yield LLMChunk(message_id=message_id, delta=text)

                        elif delta_type == "input_json_delta":
                            partial_json = delta.get("partial_json", "")
                            cur = open_tool_blocks.get(block_index)
                            if cur is not None and partial_json:
                                cur["arguments_json"] = (
                                    cur.get("arguments_json", "") + partial_json
                                )

                    elif event_type == "content_block_start":
                        content_block = data.get("content_block", {})
                        block_type = content_block.get("type", "")
                        block_index = int(data.get("index", 0) or 0)
                        if block_type == "tool_use":
                            open_tool_blocks[block_index] = {
                                "id": content_block.get("id", ""),
                                "name": content_block.get("name", ""),
                                "arguments_json": "",
                            }

                    elif event_type == "content_block_stop":
                        block_index = int(data.get("index", 0) or 0)
                        current_tool_call = open_tool_blocks.pop(block_index, None)
                        if current_tool_call is not None:
                            try:
                                args = json.loads(
                                    current_tool_call.get("arguments_json", "{}")
                                )
                            except json.JSONDecodeError:
                                args = {}
                            if not isinstance(args, dict):
                                args = {"value": args}
                            tc = ToolCall(
                                id=current_tool_call.get("id")
                                or f"toolu_{uuid.uuid4().hex[:12]}",
                                name=current_tool_call.get("name", ""),
                                arguments=args,
                            )
                            tool_calls_list.append(tc)
                            yield LLMChunk(message_id=message_id, delta="", tool_call=tc)

                    elif event_type == "message_stop":
                        finish_reason = "tool_calls" if tool_calls_list else "stop"
                        yield LLMChunk(
                            message_id=message_id,
                            delta="",
                            finish_reason=finish_reason,
                            usage=stream_usage,
                        )
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
