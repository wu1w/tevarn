"""LLM provider abstractions — non-stream + SSE stream."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

EventCallback = Callable[[dict[str, Any]], None]


@dataclass
class LLMResponse:
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    raw: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None

    @property
    def text(self) -> str:
        return self.content or ""


@dataclass
class StreamDelta:
    """One SSE / synthetic stream chunk."""

    content: str | None = None
    reasoning: str | None = None
    # accumulated snapshot of tool_calls so far (full args strings)
    tool_calls: list[dict[str, Any]] | None = None
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None
    raw_chunk: dict[str, Any] | None = None


class LLMProvider(ABC):
    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamDelta]:
        """Default: one-shot chat wrapped as a single delta batch."""
        resp = await self.chat(
            messages, tools=tools, temperature=temperature, max_tokens=max_tokens
        )
        if resp.reasoning_content:
            yield StreamDelta(reasoning=resp.reasoning_content)
        if resp.content:
            yield StreamDelta(content=resp.content)
        if resp.tool_calls:
            yield StreamDelta(tool_calls=resp.tool_calls)
        yield StreamDelta(
            finish_reason=resp.finish_reason or "stop",
            usage=resp.usage,
            tool_calls=resp.tool_calls or None,
            content=None,
        )

    async def close(self) -> None:
        return None


def _sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strict-gateway safe messages + Anthropic tool-pair integrity."""
    from takton_code.context.compressor import ensure_anthropic_strict

    messages = ensure_anthropic_strict(messages)
    out: list[dict[str, Any]] = []
    for m in messages:
        mc = dict(m)
        role = mc.get("role")
        content = mc.get("content")
        tcs = mc.get("tool_calls")
        if role == "assistant" and tcs:
            if content == "" or content is None:
                mc["content"] = None
            new_tcs = []
            for tc in tcs:
                tc2 = dict(tc)
                fn = dict(tc2.get("function") or {})
                fn["arguments"] = _truncate_tool_arguments(fn.get("arguments", ""), max_len=30000)
                tc2["function"] = fn
                if "id" not in tc2:
                    tc2["id"] = f"call_{len(new_tcs)}"
                if "type" not in tc2:
                    tc2["type"] = "function"
                new_tcs.append(tc2)
            mc["tool_calls"] = new_tcs
        elif role == "tool":
            if mc.get("content") is None:
                mc["content"] = ""
            if not mc.get("tool_call_id"):
                # should have been stripped by ensure_anthropic_strict
                continue
            # keep tool content as a plain string (never partial JSON)
            c = mc.get("content")
            if not isinstance(c, str):
                mc["content"] = json.dumps(c, ensure_ascii=False) if c is not None else ""
                c = mc["content"]
            if len(c) > 120_000:
                mc["content"] = c[:120_000] + f"\n…[sanitize trimmed {len(c) - 120_000} chars]"
        elif content is None and role != "assistant":
            mc["content"] = ""
        # keep list content (multimodal user parts) as-is
        out.append(mc)
    # second pass after content nulling
    return ensure_anthropic_strict(out)


def _truncate_tool_arguments(args: Any, max_len: int = 30000) -> str:
    """Truncate tool call arguments while keeping a JSON-parseable string when possible."""
    if isinstance(args, dict):
        s = json.dumps(args, ensure_ascii=False)
        if len(s) <= max_len:
            return s
        # structured truncation — still valid JSON
        return json.dumps(
            {
                "_truncated": True,
                "original_chars": len(s),
                "preview": s[: max(0, max_len - 120)],
            },
            ensure_ascii=False,
        )
    if not isinstance(args, str):
        args = json.dumps(args, ensure_ascii=False)
    if len(args) <= max_len:
        return args
    # try keep valid JSON object/array if original was JSON
    try:
        obj = json.loads(args)
        return _truncate_tool_arguments(obj, max_len=max_len)
    except Exception:
        # plain string payload — wrap as valid JSON string
        preview = args[: max(0, max_len - 80)]
        return json.dumps(
            {"_truncated": True, "original_chars": len(args), "preview": preview},
            ensure_ascii=False,
        )


_THINK_RE = re.compile(r"<think>[\s\S]*?</think>", re.I)


def _norm_tool_calls(tool_calls: list[Any]) -> list[dict[str, Any]]:
    norm_tc: list[dict[str, Any]] = []
    for tc in tool_calls:
        if hasattr(tc, "model_dump"):
            norm_tc.append(tc.model_dump())
        else:
            norm_tc.append(dict(tc))
    return norm_tc


def _message_from_choice(choice: dict[str, Any], *, strip_thinking: bool) -> LLMResponse:
    msg = choice.get("message") or {}
    content = msg.get("content")
    reasoning = msg.get("reasoning_content")
    if content and strip_thinking:
        content = _THINK_RE.sub("", content).strip() or content
    return LLMResponse(
        content=content,
        reasoning_content=reasoning,
        tool_calls=_norm_tool_calls(msg.get("tool_calls") or []),
        finish_reason=choice.get("finish_reason"),
    )


class _ToolCallAccumulator:
    """Merge OpenAI streaming tool_call deltas by index."""

    def __init__(self) -> None:
        self._by_idx: dict[int, dict[str, Any]] = {}

    def ingest(self, deltas: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        if not deltas:
            return self.snapshot()
        for d in deltas:
            idx = int(d.get("index") if d.get("index") is not None else 0)
            slot = self._by_idx.setdefault(
                idx,
                {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                },
            )
            if d.get("id"):
                slot["id"] = d["id"]
            if d.get("type"):
                slot["type"] = d["type"]
            fn = d.get("function") or {}
            if fn.get("name"):
                slot["function"]["name"] = (slot["function"].get("name") or "") + str(fn["name"])
            if fn.get("arguments"):
                slot["function"]["arguments"] = (slot["function"].get("arguments") or "") + str(
                    fn["arguments"]
                )
        return self.snapshot()

    def snapshot(self) -> list[dict[str, Any]]:
        out = []
        for i in sorted(self._by_idx):
            tc = self._by_idx[i]
            item = {
                "id": tc.get("id") or f"call_{i}",
                "type": tc.get("type") or "function",
                "function": {
                    "name": (tc.get("function") or {}).get("name") or "",
                    "arguments": (tc.get("function") or {}).get("arguments") or "",
                },
            }
            out.append(item)
        return out


def parse_sse_data_lines(chunk_text: str) -> list[str]:
    """Extract data payloads from a raw SSE buffer fragment (may be partial)."""
    payloads: list[str] = []
    for line in chunk_text.splitlines():
        line = line.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            payloads.append(line[5:].strip())
    return payloads


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        base_url: str,
        api_key: str = "no-key",
        model: str = "default",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout: float = 300.0,
        strip_thinking_tags: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/v1") and not self.base_url.endswith("/v1/"):
            if not re.search(r"/v\d+$", self.base_url):
                self.base_url = self.base_url + "/v1"
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.strip_thinking_tags = strip_thinking_tags
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _payload(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        temperature: float | None,
        max_tokens: int | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": _sanitize_messages(messages),
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if stream:
            # request usage in final chunk when supported
            payload["stream_options"] = {"include_usage": True}
        return payload

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        payload = self._payload(
            messages, tools, temperature=temperature, max_tokens=max_tokens, stream=False
        )
        r = await self._client.post("/chat/completions", json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"LLM HTTP {r.status_code}: {r.text[:800]}")
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        resp = _message_from_choice(choice, strip_thinking=self.strip_thinking_tags)
        resp.raw = data
        resp.usage = data.get("usage")
        return resp

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamDelta]:
        payload = self._payload(
            messages, tools, temperature=temperature, max_tokens=max_tokens, stream=True
        )
        acc = _ToolCallAccumulator()
        finish: str | None = None
        usage: dict[str, Any] | None = None
        try:
            async with self._client.stream("POST", "/chat/completions", json=payload) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode("utf-8", errors="replace")[:800]
                    raise RuntimeError(f"LLM HTTP {r.status_code}: {body}")
                buf = ""
                async for raw in r.aiter_text():
                    buf += raw
                    # process complete lines; keep remainder
                    if "\n" not in buf:
                        continue
                    *lines, buf = buf.split("\n")
                    for line in lines:
                        line = line.strip()
                        if not line or line.startswith(":"):
                            continue
                        if not line.startswith("data:"):
                            continue
                        data_s = line[5:].strip()
                        if data_s == "[DONE]":
                            yield StreamDelta(
                                finish_reason=finish or "stop",
                                usage=usage,
                                tool_calls=acc.snapshot() or None,
                            )
                            return
                        try:
                            chunk = json.loads(data_s)
                        except json.JSONDecodeError:
                            continue
                        if chunk.get("usage"):
                            usage = chunk["usage"]
                        choice = (chunk.get("choices") or [{}])[0]
                        if choice.get("finish_reason"):
                            finish = choice["finish_reason"]
                        delta = choice.get("delta") or {}
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                        content = delta.get("content")
                        if content and self.strip_thinking_tags:
                            # don't strip mid-stream partial tags aggressively
                            pass
                        tc_delta = delta.get("tool_calls")
                        snap = acc.ingest(tc_delta) if tc_delta else None
                        if reasoning or content or tc_delta:
                            yield StreamDelta(
                                content=content,
                                reasoning=reasoning,
                                tool_calls=snap,
                                raw_chunk=chunk,
                            )
                # flush leftover
                for data_s in parse_sse_data_lines(buf):
                    if data_s == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_s)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    choice = (chunk.get("choices") or [{}])[0]
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
                    delta = choice.get("delta") or {}
                    if delta.get("content") or delta.get("reasoning_content") or delta.get("tool_calls"):
                        snap = acc.ingest(delta.get("tool_calls"))
                        yield StreamDelta(
                            content=delta.get("content"),
                            reasoning=delta.get("reasoning_content"),
                            tool_calls=snap if delta.get("tool_calls") else None,
                        )
        except RuntimeError:
            raise
        except Exception:
            # fallback non-stream
            resp = await self.chat(
                messages, tools=tools, temperature=temperature, max_tokens=max_tokens
            )
            if resp.reasoning_content:
                yield StreamDelta(reasoning=resp.reasoning_content)
            if resp.content:
                yield StreamDelta(content=resp.content)
            if resp.tool_calls:
                yield StreamDelta(tool_calls=resp.tool_calls)
            yield StreamDelta(
                finish_reason=resp.finish_reason or "stop",
                usage=resp.usage,
                tool_calls=resp.tool_calls or None,
            )
            return

        yield StreamDelta(
            finish_reason=finish or "stop",
            usage=usage,
            tool_calls=acc.snapshot() or None,
        )


class BridgeLLMProvider(LLMProvider):
    """LLM via Takton Desktop bridge — stream when backend supports SSE."""

    def __init__(
        self, bridge: Any, model: str, temperature: float = 0.2, max_tokens: int = 4096
    ) -> None:
        self.bridge = bridge
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        from takton_code.bridge.protocol import ChatMessage, ChatRequest

        req = ChatRequest(
            model=self.model,
            messages=[ChatMessage.model_validate(m) for m in _sanitize_messages(messages)],
            tools=tools,
            temperature=self.temperature if temperature is None else temperature,
            max_tokens=self.max_tokens if max_tokens is None else max_tokens,
            stream=False,
        )
        data = await self.bridge.chat(req)
        choice = (data.get("choices") or [{}])[0]
        resp = _message_from_choice(choice, strip_thinking=True)
        resp.raw = data
        resp.usage = data.get("usage")
        return resp

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamDelta]:
        # Prefer bridge SSE if client exposes chat_stream; else synthetic.
        stream_fn = getattr(self.bridge, "chat_stream", None)
        if callable(stream_fn):
            from takton_code.bridge.protocol import ChatMessage, ChatRequest

            req = ChatRequest(
                model=self.model,
                messages=[ChatMessage.model_validate(m) for m in _sanitize_messages(messages)],
                tools=tools,
                temperature=self.temperature if temperature is None else temperature,
                max_tokens=self.max_tokens if max_tokens is None else max_tokens,
                stream=True,
            )
            acc = _ToolCallAccumulator()
            finish = None
            usage = None
            async for chunk in stream_fn(req):  # type: ignore[misc]
                if not isinstance(chunk, dict):
                    continue
                if chunk.get("usage"):
                    usage = chunk["usage"]
                choice = (chunk.get("choices") or [{}])[0]
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
                delta = choice.get("delta") or choice.get("message") or {}
                # non-stream shaped chunk
                if "tool_calls" in delta and choice.get("message"):
                    yield StreamDelta(
                        content=delta.get("content"),
                        reasoning=delta.get("reasoning_content"),
                        tool_calls=_norm_tool_calls(delta.get("tool_calls") or []),
                    )
                    continue
                snap = acc.ingest(delta.get("tool_calls")) if delta.get("tool_calls") else None
                if delta.get("content") or delta.get("reasoning_content") or delta.get("tool_calls"):
                    yield StreamDelta(
                        content=delta.get("content"),
                        reasoning=delta.get("reasoning_content"),
                        tool_calls=snap,
                    )
            yield StreamDelta(
                finish_reason=finish or "stop",
                usage=usage,
                tool_calls=acc.snapshot() or None,
            )
            return

        async for d in super().chat_stream(
            messages, tools=tools, temperature=temperature, max_tokens=max_tokens
        ):
            yield d


async def collect_stream(
    provider: LLMProvider,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    on_delta: EventCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> LLMResponse:
    """Consume chat_stream into LLMResponse; optional live callbacks."""
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    finish: str | None = None
    usage: dict[str, Any] | None = None
    raw_last: dict[str, Any] | None = None

    async for d in provider.chat_stream(
        messages, tools=tools, temperature=temperature, max_tokens=max_tokens
    ):
        if should_cancel and should_cancel():
            finish = finish or "cancelled"
            break
        if d.raw_chunk:
            raw_last = d.raw_chunk
        if d.reasoning:
            reasoning_parts.append(d.reasoning)
            if on_delta:
                on_delta({"type": "reasoning_delta", "text": d.reasoning})
        if d.content:
            content_parts.append(d.content)
            if on_delta:
                on_delta({"type": "text_delta", "text": d.content})
        if d.tool_calls is not None:
            tool_calls = d.tool_calls
            if on_delta:
                on_delta({"type": "tool_calls_delta", "tool_calls": tool_calls})
        if d.finish_reason:
            finish = d.finish_reason
        if d.usage:
            usage = d.usage

    content = "".join(content_parts) or None
    reasoning = "".join(reasoning_parts) or None
    if content and _THINK_RE.search(content):
        content = _THINK_RE.sub("", content).strip() or content

    return LLMResponse(
        content=content,
        reasoning_content=reasoning,
        tool_calls=tool_calls,
        finish_reason=finish,
        raw=raw_last,
        usage=usage,
    )


def build_llm_provider(
    *,
    base_url: str,
    api_key: str,
    model: str,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    bridge: Any | None = None,
    use_bridge: bool = False,
) -> LLMProvider:
    if use_bridge and bridge is not None and getattr(bridge, "enabled", False):
        return BridgeLLMProvider(
            bridge, model=model, temperature=temperature, max_tokens=max_tokens
        )
    return OpenAICompatibleProvider(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
