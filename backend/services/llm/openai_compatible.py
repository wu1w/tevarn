"""
通用 OpenAI 兼容 LLM 服务实现
支持 vLLM、TGI、llama.cpp server、LM Studio、Text Generation Inference 等
任何遵循 OpenAI /v1/chat/completions 格式的本地或远程服务
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from backend.core.config import settings

from .interface import LLMService
from .schemas import LLMChunk, LLMResponse, ToolCall

logger = logging.getLogger(__name__)


def merge_stream_tool_delta(
    accumulated: dict[int, dict[str, Any]], tc: dict[str, Any]
) -> None:
    """Merge one OpenAI stream tool_calls[] delta into index-keyed accumulator.

    Official Chat Completions streaming: each delta carries `index`; id/name may
    arrive on first chunk and arguments are concatenated across chunks.

    Gemini OpenAI-compat may attach ``extra_content.google.thought_signature`` on
    tool_call parts — preserve for multi-turn function calling (required on Gemini 3).
    """
    index = int(tc.get("index", 0) or 0)
    fn = tc.get("function") or {}
    if index not in accumulated:
        accumulated[index] = {
            "id": tc.get("id") or "",
            "name": fn.get("name") or "",
            "arguments": fn.get("arguments") or "",
        }
        if tc.get("extra_content") is not None:
            accumulated[index]["extra_content"] = tc.get("extra_content")
        if tc.get("thought_signature") is not None:
            accumulated[index]["thought_signature"] = tc.get("thought_signature")
        return
    entry = accumulated[index]
    if tc.get("id"):
        entry["id"] = tc["id"]
    if fn.get("name"):
        entry["name"] = fn["name"]
    if fn.get("arguments"):
        entry["arguments"] = (entry.get("arguments") or "") + fn["arguments"]
    if tc.get("extra_content") is not None:
        entry["extra_content"] = tc.get("extra_content")
    if tc.get("thought_signature") is not None:
        entry["thought_signature"] = tc.get("thought_signature")


def tool_call_to_openai_message(tc: ToolCall | Any) -> dict[str, Any]:
    """Serialize ToolCall for the next-turn assistant.tool_calls array.

    Gemini OpenAI-compat requires ``extra_content`` / ``thought_signature`` on
    the function-call object in subsequent requests (Gemini 3 multi-turn).
    """
    args = getattr(tc, "arguments", None)
    if not isinstance(args, str):
        try:
            args_s = json.dumps(args if args is not None else {}, ensure_ascii=False)
        except Exception:
            args_s = "{}"
    else:
        args_s = args
    item: dict[str, Any] = {
        "id": str(getattr(tc, "id", "") or f"call_{uuid.uuid4().hex[:8]}"),
        "type": "function",
        "function": {
            "name": str(getattr(tc, "name", "") or ""),
            "arguments": args_s,
        },
    }
    extra = getattr(tc, "extra_content", None)
    sig = getattr(tc, "thought_signature", None)
    if extra is not None and isinstance(extra, dict):
        item["extra_content"] = extra
    if sig:
        item["thought_signature"] = str(sig)
        if "extra_content" not in item:
            item["extra_content"] = {"google": {"thought_signature": str(sig)}}
    return item


def _tool_call_from_openai(tc: dict[str, Any]) -> ToolCall:
    """Build ToolCall preserving Gemini thought_signature / extra_content."""
    fn = tc.get("function") or {}
    args_raw = fn.get("arguments") or "{}"
    if isinstance(args_raw, dict):
        args = args_raw
    else:
        try:
            args = json.loads(args_raw) if args_raw else {}
        except Exception:
            args = {"_raw": str(args_raw)[:4000]}
    extra = tc.get("extra_content")
    if extra is not None and not isinstance(extra, dict):
        extra = None
    sig = tc.get("thought_signature")
    if sig is None and isinstance(extra, dict):
        try:
            sig = (extra.get("google") or {}).get("thought_signature")
        except Exception:
            sig = None
    return ToolCall(
        id=str(tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"),
        name=str(fn.get("name") or ""),
        arguments=args if isinstance(args, dict) else {},
        extra_content=extra,
        thought_signature=str(sig) if sig else None,
    )


class OpenAIStreamAccumulator:
    """Merge Chat Completions SSE deltas; delay finish until stream end.

    OpenAI ``stream_options.include_usage`` delivers usage in a later chunk with
    empty ``choices`` *after* ``finish_reason``. Stopping on finish_reason drops
    that usage (and can hide trailing tool-call deltas on some gateways).
    """

    def __init__(self, message_id: uuid.UUID, *, normalize_usage=None):
        self.message_id = message_id
        self.accumulated_tool_calls: dict[int, dict[str, Any]] = {}
        self.last_finish_reason: str | None = None
        self.stream_usage: dict[str, int] = {}
        self._normalize_usage = normalize_usage or (lambda u: u if isinstance(u, dict) else {})
        self._tools_emitted = False
        self._finish_emitted = False

    def consume_data_line(self, payload_s: str) -> tuple[bool, list[LLMChunk]]:
        """Parse one SSE data payload → (stream_done, chunks).

        ``stream_done`` is True only for ``[DONE]``. ``finish_reason`` is recorded
        but does not stop the stream.
        """
        if payload_s == "[DONE]":
            return True, self.finalize()
        if not payload_s:
            return False, []
        try:
            data = json.loads(payload_s)
        except json.JSONDecodeError:
            return False, []

        raw_usage = data.get("usage")
        if isinstance(raw_usage, dict) and raw_usage:
            mapped = self._normalize_usage(raw_usage)
            if isinstance(mapped, dict) and mapped:
                self.stream_usage.update(mapped)

        choices = data.get("choices") or []
        if not choices:
            return False, []
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta", {}) or {}

        chunks_out: list[LLMChunk] = []
        content = delta.get("content", "") or ""
        if content:
            chunks_out.append(LLMChunk(message_id=self.message_id, delta=content))

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
            chunks_out.append(
                LLMChunk(
                    message_id=self.message_id,
                    delta="",
                    reasoning_delta=str(reasoning),
                )
            )

        for tc in delta.get("tool_calls") or []:
            if isinstance(tc, dict):
                merge_stream_tool_delta(self.accumulated_tool_calls, tc)

        finish_reason = choice.get("finish_reason")
        if finish_reason:
            self.last_finish_reason = finish_reason
        return False, chunks_out

    def _emit_tool_calls(self) -> list[LLMChunk]:
        if self._tools_emitted:
            return []
        out: list[LLMChunk] = []
        for tc_data in self.accumulated_tool_calls.values():
            name = (tc_data.get("name") or "").strip()
            if not name:
                logger.warning("Skipping stream tool_call with empty name: %s", tc_data)
                continue
            shaped = {
                "id": tc_data.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                "function": {
                    "name": name,
                    "arguments": tc_data.get("arguments") or "{}",
                },
            }
            if tc_data.get("extra_content") is not None:
                shaped["extra_content"] = tc_data.get("extra_content")
            if tc_data.get("thought_signature") is not None:
                shaped["thought_signature"] = tc_data.get("thought_signature")
            out.append(
                LLMChunk(
                    message_id=self.message_id,
                    delta="",
                    tool_call=_tool_call_from_openai(shaped),
                )
            )
        self._tools_emitted = True
        return out

    def finalize(self) -> list[LLMChunk]:
        """Flush tool calls + finish_reason with any usage collected so far."""
        if self._finish_emitted:
            return []
        out = self._emit_tool_calls()
        if self.accumulated_tool_calls and any(
            (v.get("name") or "").strip() for v in self.accumulated_tool_calls.values()
        ):
            effective = "tool_calls"
        else:
            effective = self.last_finish_reason or "stop"
        out.append(
            LLMChunk(
                message_id=self.message_id,
                delta="",
                finish_reason=effective,
                usage=dict(self.stream_usage) if self.stream_usage else {},
            )
        )
        self._finish_emitted = True
        return out


class OpenAICompatibleService(LLMService):
    """通用 OpenAI 兼容 LLM 服务"""

    def __init__(self, config=None, *, profile=None, provider_id: str | None = None,
                 prompt_cache_key: str | None = None):
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
        self.reasoning_effort = str(
            getattr(self.config, "reasoning_effort", None)
            or getattr(settings, "reasoning_effort", "medium")
            or "medium"
        ).strip().lower() or "medium"
        self.api_key = getattr(self.config, "api_key", None)
        self.provider_id = (provider_id or getattr(config, "provider_id", None) or "").strip()
        self.prompt_cache_key = prompt_cache_key or getattr(config, "prompt_cache_key", None)

        # Provider family profile (cache / limits / meter)
        if profile is not None:
            self.profile = profile
        else:
            from .provider_profiles import resolve_profile
            self.profile = resolve_profile(
                provider_id=self.provider_id or None,
                base_url=self.base_url,
                model=self.model,
                llm_provider="openai-compatible",
            )
        self._max_tool_arg_chars = int(
            getattr(self.profile, "max_tool_arg_chars", self._MAX_TOOL_ARG_CHARS)
        )
        self._max_tool_result_chars = int(
            getattr(self.profile, "max_tool_result_chars", self._MAX_TOOL_RESULT_CHARS)
        )

        # Kimi Code 仅接受 temperature=1：会话快照/ephemeral snapshot 可能带 0.7
        # 等其它值，经 get_service_for_snapshot 锁进实例后直接 400。在此强制钳制，
        # 覆盖主路径与 bridge 等所有调用入口（与 _normalize_model_id 同一判定）。
        if self._is_kimi_coding() and self.temperature != 1.0:
            logger.warning(
                "Kimi Code only accepts temperature=1; overriding %r -> 1.0",
                self.temperature,
            )
            self.temperature = 1.0
        elif (
            getattr(self.profile, "force_temperature", None) is not None
            and self.temperature != float(self.profile.force_temperature)
        ):
            self.temperature = float(self.profile.force_temperature)

    def _is_kimi_coding(self) -> bool:
        """与 _normalize_model_id 同一 Kimi Code 判定。"""
        b = (self.base_url or "").lower()
        return "kimi.com/coding" in b or "api.kimi.com/coding" in b

    def _family(self) -> str:
        """用量/缓存归类键：优先 catalog 供应商 id，其次 URL / 模型启发式。

        显示层必须反映**真实请求路径**（provider_id + model），不能因为
        会话旧快照或 profile 默认族把 gpt-5.6-luna 记成 deepseek。
        """
        # 1) service / settings 上的 catalog id（openai-chatgpt-oauth / opencode-go …）
        for pid in (
            (self.provider_id or "").strip(),
            str(getattr(settings, "llm_catalog_provider_id", "") or "").strip(),
        ):
            if pid and pid.lower() not in (
                "",
                "custom",
                "generic",
                "openai-compatible",
                "openai_compatible",
            ):
                return pid

        # 2) URL 特判：本机 Codex 代理 → 固定显示 openai-chatgpt-oauth
        b = (self.base_url or "").lower()
        if (
            "openai-codex" in b
            or "llm-proxy/openai" in b
            or "chatgpt.com" in b
            or "backend-api/codex" in b
        ):
            return "openai-chatgpt-oauth"

        # 3) OpenCode 网关：用网关 id，避免把 deepseek/gpt 模型名盖掉供应商
        try:
            from .provider_profiles import _family_from_model, _family_from_url

            by_u = _family_from_url(self.base_url)
            if by_u == "opencode":
                return "opencode-go"
            if by_u and by_u not in ("generic",):
                return by_u
            by_m = _family_from_model(self.model)
            if by_m and by_m not in ("generic",):
                return by_m
        except Exception:
            pass

        fam = str(getattr(self.profile, "family", "") or "").strip()
        return fam or "generic"

    def _normalize_usage(self, raw: dict[str, Any] | None) -> dict[str, int]:
        """Normalize only — ledger write is once per round in llm_round.record_round_usage."""
        from .usage_normalize import map_responses_usage_to_openai, normalize_usage

        mapped = map_responses_usage_to_openai(raw if isinstance(raw, dict) else None)
        return normalize_usage(mapped or raw, family=self._family())

    def _apply_profile_payload_hooks(self, payload: dict[str, Any], messages: list[dict[str, Any]]) -> None:
        """stream_options / prompt_cache_key / optional explicit cache_control."""
        from .cache_protocol import (
            apply_anthropic_style_cache,
            apply_openai_prompt_cache_key,
            reorder_tools_before_system_messages,
        )
        from .provider_profiles import explicit_cache_enabled

        prof = self.profile
        if payload.get("stream"):
            # OpenAI docs: usage only on final chunk when stream_options.include_usage=true
            # Safe no-op on gateways that ignore unknown fields.
            so = payload.get("stream_options")
            if not isinstance(so, dict):
                so = {}
            if getattr(prof, "stream_include_usage", True) or True:
                so = {**so, "include_usage": True}
            payload["stream_options"] = so

        # OpenAI official cache key
        try:
            key_on = bool(getattr(settings, "agent_prompt_cache_openai_key", True))
        except Exception:
            key_on = True
        if getattr(prof, "openai_prompt_cache_key", False) and key_on:
            # Prefer session-stable key (tevarn:{session_id}); hash only as last resort.
            key = str(self.prompt_cache_key or "").strip()
            if key and not key.startswith("tevarn:") and len(key) >= 32:
                # bare session uuid from older callers → normalize
                key = f"tevarn:{key[:32]}"
            if not key:
                try:
                    import hashlib

                    sys0 = ""
                    for m in messages or []:
                        if isinstance(m, dict) and m.get("role") == "system":
                            sys0 = str(m.get("content") or "")[:4000]
                            break
                    tools_sig = ""
                    for t in (payload.get("tools") or [])[:40]:
                        if isinstance(t, dict):
                            fn = t.get("function") if isinstance(t.get("function"), dict) else t
                            tools_sig += str((fn or {}).get("name") or "") + ","
                    raw = f"{self.model}|{sys0[:1500]}|{tools_sig}"
                    key = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:32]
                except Exception:
                    key = ""
            apply_openai_prompt_cache_key(
                payload,
                cache_key=key or None,
                enabled=True,
            )

        if explicit_cache_enabled(prof):
            # OpenAI-shaped tools must not get cache_control (400 on many gateways).
            # Only mark system + penultimate message content blocks.
            apply_anthropic_style_cache(
                payload,
                messages,
                max_breakpoints=int(getattr(prof, "cache_max_breakpoints", 4) or 4),
                enabled=True,
                mark_tools=False,
            )
            payload["_tevarn_explicit_cache"] = True

        if getattr(prof, "tools_before_system", False):
            reorder_tools_before_system_messages(payload)

        # 思考强度 → 各家 API 字段（不支持的模型自动跳过）
        try:
            from .reasoning_effort import apply_reasoning_effort

            apply_reasoning_effort(
                payload,
                effort=getattr(self, "reasoning_effort", None),
                model=self.model,
                family=str(getattr(prof, "family", "") or ""),
            )
        except Exception:
            logger.debug("apply_reasoning_effort skipped", exc_info=True)

    @staticmethod
    def resolve_effective_model_id(model: str, base_url: str) -> str:
        """公开：选用名 → 上游实际 model id（供 UI / 快路径回显）。"""
        return OpenAICompatibleService._normalize_model_id(model, base_url)

    @staticmethod
    def _normalize_model_id(model: str, base_url: str) -> str:
        """Kimi Code 仅接受 kimi-for-coding / kimi-for-coding-highspeed。"""
        m = (model or "").strip()
        b = (base_url or "").lower()
        if "kimi.com/coding" in b or "api.kimi.com/coding" in b:
            aliases = {
                "k3": "kimi-for-coding",
                "k3-256k": "kimi-for-coding",
                "k3_256k": "kimi-for-coding",
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
        # ChatGPT OAuth / Codex 订阅路径需要 Account-Id
        try:
            aid = str(getattr(settings, "openai_chatgpt_account_id", "") or "").strip()
            if not aid:
                from backend.core.config import settings as _s

                aid = str(getattr(_s, "openai_chatgpt_account_id", "") or "").strip()
            if aid and (
                "openai-codex" in (self.base_url or "")
                or "chatgpt.com" in (self.base_url or "")
                or self.provider_id in ("openai-chatgpt-oauth", "openai-codex-oauth")
            ):
                headers["ChatGPT-Account-Id"] = aid
        except Exception:
            pass
        return headers

    def _chat_completions_url(self) -> str:
        """兼容 base_url 已含版本号（/v1 /v2 /v4 /api 等）的写法，避免拼出 /v1/v1/... 或 /v2/v1/..."""
        import re as _re
        base = self.base_url.rstrip("/")
        if _re.search(r"/(v\d+|api)$", base):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def _normalize_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize + sort tools for stable automatic prefix cache.

        OpenAI/DeepSeek/xAI automatic caching requires the request prefix
        (tools + early messages) to be byte-identical across rounds.
        """
        normalized: list[dict[str, Any]] = []

        def _stable_params(fn: dict[str, Any]) -> dict[str, Any]:
            params = fn.get("parameters")
            if isinstance(params, dict):
                try:
                    fn["parameters"] = json.loads(
                        json.dumps(params, sort_keys=True, ensure_ascii=False)
                    )
                except Exception:
                    pass
            return fn

        for t in tools or []:
            if not isinstance(t, dict):
                continue
            # Already OpenAI-shaped: {type, function}
            if isinstance(t.get("function"), dict):
                fn = _stable_params(dict(t["function"]))
                normalized.append({"type": "function", "function": fn})
                continue
            # Bare function body: {name, description, parameters}
            if t.get("name"):
                fn = _stable_params(dict(t))
                normalized.append({"type": "function", "function": fn})
                continue
            # Fallback
            fn = _stable_params(dict(t))
            normalized.append({"type": "function", "function": fn})

        def _name(item: dict[str, Any]) -> str:
            fn = item.get("function") if isinstance(item.get("function"), dict) else {}
            return str((fn or {}).get("name") or "")

        normalized.sort(key=_name)
        return normalized

    # 部分兼容网关（讯飞 MaaS 等）拒绝：assistant 空字符串 content + tool_calls
    # 以及超大历史 tool arguments。统一在出站前消毒。
    _MAX_TOOL_ARG_CHARS = 6000
    _MAX_TOOL_RESULT_CHARS = 12000

    def _sanitize_messages_for_api(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize message list for strict OpenAI-compatible gateways.

        Critical: function.arguments MUST remain valid JSON after any truncation
        (iFlytek MaaS returns 400 otherwise).
        """
        out: list[dict[str, Any]] = []
        pending_tool_ids: set[str] = set()
        missing_reasoning_n = 0

        # 预扫描：收集本序列中所有 assistant.tool_calls 声明的 tool_call_id。
        # 用于识别"孤儿 tool 消息"——引用了不存在 tool_calls 的 tool 消息。
        # 这类孤儿多由历史压缩（L3/L5）剥掉 assistant.tool_calls 但残留 tool 消息造成，
        # 会被严格 OpenAI 兼容网关（如 Kimi）以 400 拒绝，必须在发送前剔除。
        declared_tool_ids: set[str] = set()
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "assistant":
                tcs = m.get("tool_calls")
                if isinstance(tcs, list):
                    for tc in tcs:
                        if isinstance(tc, dict) and tc.get("id"):
                            declared_tool_ids.add(str(tc["id"]))

        arg_limit = int(
            getattr(self, "_max_tool_arg_chars", None)
            or getattr(self, "_MAX_TOOL_ARG_CHARS", 6000)
            or 6000
        )
        result_limit = int(
            getattr(self, "_max_tool_result_chars", None)
            or getattr(self, "_MAX_TOOL_RESULT_CHARS", 12000)
            or 12000
        )

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

            if len(raw) <= arg_limit and parsed is not None:
                # re-dump to guarantee compact valid JSON
                try:
                    return json.dumps(parsed, ensure_ascii=False)
                except Exception:
                    return raw if len(raw) <= arg_limit else json.dumps(
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
                # DeepSeek V4 等：thinking 模式下 tool 轮必须回传 reasoning_content
                rc = m.get("reasoning_content")
                if not (isinstance(rc, str) and rc.strip()):
                    alt = m.get("reasoning")
                    if isinstance(alt, str) and alt.strip():
                        rc = alt
                    else:
                        rc = ""
                # Stash in tool_calls JSON before extracting <thinking> from content
                try:
                    from backend.agent.thinking_format import (
                        split_reasoning_from_tool_calls,
                    )

                    tcs_clean, stashed_rc = split_reasoning_from_tool_calls(tcs)
                    if tcs_clean is not None:
                        tcs = tcs_clean
                        m["tool_calls"] = tcs_clean
                    if stashed_rc and not (isinstance(rc, str) and rc.strip()):
                        rc = stashed_rc
                except Exception:
                    pass
                if not (isinstance(rc, str) and rc.strip()) and isinstance(content, str) and content:
                    try:
                        from backend.agent.thinking_format import (
                            extract_reasoning_content,
                        )

                        rc = extract_reasoning_content(content)
                    except Exception:
                        rc = ""
                if isinstance(rc, str) and rc.strip():
                    m["reasoning_content"] = rc.strip()
                else:
                    m.pop("reasoning_content", None)
                    m.pop("reasoning", None)
                if tcs:
                    if content is None or (isinstance(content, str) and not content.strip()):
                        m["content"] = None
                    elif isinstance(content, str) and ("<thinking" in content.lower() or "<think" in content.lower()):
                        # 出站 content 去掉 thinking 标签，reasoning 走独立字段
                        try:
                            from backend.agent.thinking_format import strip_thinking

                            body = strip_thinking(content)
                            m["content"] = body if body.strip() else None
                        except Exception:
                            pass
                    new_tcs: list[dict[str, Any]] = []
                    if isinstance(tcs, list):
                        for tc in tcs:
                            if not isinstance(tc, dict):
                                continue
                            tc2 = dict(tc)
                            tc2.pop("_tevarn_reasoning", None)
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
                    if not m.get("reasoning_content"):
                        missing_reasoning_n += 1
                else:
                    if content is None:
                        m["content"] = ""
                    m.pop("tool_calls", None)
                out.append(m)
                continue

            if role == "tool":
                tid = m.get("tool_call_id")
                # 孤儿 tool 消息：其 tool_call_id 在本序列中没有任何 assistant.tool_calls 声明。
                # 直接丢弃，避免严格网关 400（历史压缩剥掉 tool_calls 后常见）。
                # 注意：declared_tool_ids 为空集时，说明序列里没有任何 tool_calls，
                # 此时所有带 id 的 tool 消息都是孤儿，同样必须丢弃。
                if tid and str(tid) not in declared_tool_ids:
                    logger.warning(
                        "dropping orphan tool message (tool_call_id=%s has no matching tool_calls)",
                        tid,
                    )
                    continue
                content = m.get("content")
                if content is None:
                    m["content"] = ""
                elif not isinstance(content, str):
                    m["content"] = str(content)
                if len(m["content"]) > result_limit:
                    m["content"] = _trim_text(m["content"], result_limit)
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

        if missing_reasoning_n:
            logger.warning(
                "assistant tool_calls without reasoning_content x%d "
                "(DeepSeek V4 thinking+tools may return HTTP 400)",
                missing_reasoning_n,
            )

        # 双向净化（对齐 hermes _sanitize_tool_pairs）：assistant.tool_calls 必须有
        # 对应 tool_result，反之亦然。发到 API 的消息是完整历史，任何 tool_calls 都
        # 应当已完结、有匹配 tool_result；缺结果的孤儿 tool_calls 只可能来自压缩
        # （L3/L5 丢了 tool_result）或异常中断。
        # OpenAI 兼容：剥掉未配对 tool_calls（stub 易被二次规则丢掉）。
        # Anthropic 侧改为注入 is_error tool_result（见 anthropic._convert_messages）——
        # 原生 Messages API 不允许「只有 tool_use、没有 tool_result」。
        # pending_tool_ids 扫描结束后仍非空 = 这些 tool_calls 到结尾都无对应 tool_result。
        if pending_tool_ids:
            stripped = 0
            for m in out:
                if m.get("role") != "assistant":
                    continue
                tcs = m.get("tool_calls")
                if not tcs:
                    continue
                kept = [
                    tc
                    for tc in tcs
                    if not (isinstance(tc, dict) and tc.get("id") and str(tc["id"]) in pending_tool_ids)
                ]
                if len(kept) != len(tcs):
                    stripped += len(tcs) - len(kept)
                    if kept:
                        m["tool_calls"] = kept
                    else:
                        m.pop("tool_calls", None)
                        # 剥光后避免空 assistant turn 被网关拒绝
                        content = m.get("content")
                        if not content or (isinstance(content, str) and not content.strip()):
                            m["content"] = "(tool call removed)"
            if stripped:
                logger.warning(
                    "stripped %d orphaned tool_call(s) with no matching tool result",
                    stripped,
                )
            pending_tool_ids.clear()

        # llama.cpp / 多数 chat template：system 只能出现在开头且通常只能一条。
        # Tevarn 会注入多段 system（主 prompt + 工具说明 + 运行时注记）→ 400
        # 「System message must be at the beginning」。合并为单条置顶。
        system_parts: list[str] = []
        non_system: list[dict[str, Any]] = []
        for m in out:
            if m.get("role") == "system":
                c = m.get("content")
                if c is None:
                    continue
                if isinstance(c, str):
                    if c.strip():
                        system_parts.append(c)
                else:
                    system_parts.append(str(c))
            else:
                non_system.append(m)
        if system_parts:
            out = [{"role": "system", "content": "\n\n".join(system_parts)}, *non_system]
        else:
            out = non_system

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
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": safe_messages,
            "stream": stream,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if tools:
            payload["tools"] = self._normalize_tools(tools)
            payload["tool_choice"] = "auto"
        self._apply_profile_payload_hooks(payload, safe_messages)
        # Do not send internal flags to provider
        payload.pop("_tevarn_explicit_cache", None)

        message_id = uuid.uuid4()

        if not stream:
            try:
                from .http_session import ensure_session, request_timeout

                session = ensure_session(self)
                async with session.post(
                    url, json=payload, headers=self._get_headers(),
                    timeout=request_timeout(),
                ) as resp:
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
                            if not isinstance(tc, dict):
                                continue
                            yield LLMChunk(
                                message_id=message_id,
                                delta="",
                                tool_call=_tool_call_from_openai(tc),
                            )
                    if reasoning:
                        yield LLMChunk(
                            message_id=message_id,
                            delta="",
                            reasoning_delta=str(reasoning),
                        )
                    _usage = self._normalize_usage(data.get("usage") if isinstance(data, dict) else None)
                    yield LLMChunk(
                        message_id=message_id,
                        delta=content or "",
                        finish_reason=finish_reason,
                        usage=_usage,
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
                    detail_l = detail.lower()
                    if "reasoning_content" in detail_l or "thinking mode" in detail_l:
                        hint = (
                            " DeepSeek/思考模式：带 tools 的多轮必须回传上一轮 assistant 的 "
                            "reasoning_content。请升级 Tevarn 或关闭该模型的 thinking/"
                            f"reasoning_effort=off。（当前 model={self.model!r}）"
                        )
                    else:
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

        acc = OpenAIStreamAccumulator(
            message_id, normalize_usage=self._normalize_usage
        )

        try:
            from .http_session import (
                ensure_session,
                first_event_timeout_seconds,
                stream_timeout,
            )
            from .sse import split_sse_data_lines

            session = ensure_session(self)
            async with session.post(
                url, json=payload, headers=self._get_headers(),
                timeout=stream_timeout(),
            ) as resp:
                resp.raise_for_status()
                # TCP 分片安全：不能把每个 chunk 当成完整 SSE 行（半行 JSON 会静默丢事件）
                sse_buf = ""
                stream_done = False
                first_to = first_event_timeout_seconds()
                deadline = (time.monotonic() + first_to) if first_to else None
                got_data = False
                body_iter = resp.content.__aiter__()

                while True:
                    if not got_data and deadline is not None:
                        remain = deadline - time.monotonic()
                        if remain <= 0:
                            raise asyncio.TimeoutError(
                                "LLM stream first SSE data event timeout"
                            )
                        try:
                            raw = await asyncio.wait_for(
                                body_iter.__anext__(), timeout=remain
                            )
                        except StopAsyncIteration:
                            break
                    else:
                        try:
                            raw = await body_iter.__anext__()
                        except StopAsyncIteration:
                            break
                    chunk = raw if isinstance(raw, (bytes, bytearray)) else bytes(raw)
                    text_s = sse_buf + chunk.decode("utf-8", errors="replace")
                    sse_buf, payloads = split_sse_data_lines(text_s)
                    if payloads:
                        got_data = True
                    for payload_s in payloads:
                        stop, chunks_out = acc.consume_data_line(payload_s)
                        for c in chunks_out:
                            yield c
                        if stop:
                            stream_done = True
                            break
                    if stream_done:
                        break
                # Residual buffer (no trailing newline on last event)
                if not stream_done and (sse_buf or "").strip():
                    _, payloads = split_sse_data_lines(sse_buf + "\n")
                    for payload_s in payloads:
                        stop, chunks_out = acc.consume_data_line(payload_s)
                        for c in chunks_out:
                            yield c
                        if stop:
                            stream_done = True
                            break
                if not stream_done:
                    for c in acc.finalize():
                        yield c

        except asyncio.TimeoutError:
            logger.error("OpenAI-compatible stream first-event timeout")
            yield LLMChunk(
                message_id=message_id,
                delta="[LLM Error] 模型流式首包超时（一直没收到 SSE data）。请重试。",
                finish_reason="error",
            )
            return

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
                detail_l = detail.lower()
                if "reasoning_content" in detail_l or "thinking mode" in detail_l:
                    hint = (
                        " DeepSeek/思考模式：带 tools 的多轮必须回传上一轮 assistant 的 "
                        "reasoning_content。请升级 Tevarn 或关闭该模型的 thinking/"
                        f"reasoning_effort=off。（当前 model={self.model!r}）"
                    )
                else:
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
        usage: dict[str, int] = {}
        for c in chunks:
            u = getattr(c, "usage", None)
            if isinstance(u, dict) and u:
                usage.update(u)

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )
