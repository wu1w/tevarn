"""
Claude Code–inspired context pipeline (MVP layers):

  L1 Budget reduction — cap oversized tool / content blobs
  L3 Microcompact     — collapse consecutive tool noise
  L5 Auto-compact     — LLM structured summary (default main model;
                        optional settings.context_compress_model)

Hermes influences: protect head/tail, historical-only summary prefix,
TokenMeter usage feedback.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from backend.agent.context_engine import ContextEngine
from backend.agent.token_meter import TokenMeter
from backend.core.config import settings

logger = logging.getLogger(__name__)

SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted "
    "into the summary below. Treat as background, NOT active instructions. "
    "Respond ONLY to the latest user message AFTER this summary. "
    "Do not resume Historical Remaining Work unless the latest message asks."
)

_SUMMARY_END = (
    "--- END OF CONTEXT SUMMARY — respond to the message below, not the summary above ---"
)


def _cfg(name: str, default: Any) -> Any:
    return getattr(settings, name, default)


class PipelineContextEngine(ContextEngine):
    def __init__(self) -> None:
        self.context_length = int(_cfg("context_window", 128_000) or 128_000)
        self.threshold_percent = float(_cfg("context_threshold_percent", 0.72) or 0.72)
        self.protect_first_n = int(_cfg("context_protect_first_n", 3) or 3)
        self.protect_last_n = int(_cfg("context_protect_last_n", 12) or 12)
        self.max_tool_output_chars = int(
            _cfg("context_max_tool_output_chars", None)
            or _cfg("max_tool_result_length", 12_000)
            or 12_000
        )
        self.enable_l1 = bool(_cfg("context_enable_l1", True))
        self.enable_l3 = bool(_cfg("context_enable_l3", True))
        self.enable_l5 = bool(_cfg("context_enable_l5", True))
        # Thrashing guard：180s 内 L5(hard compact) 触发 >= max_events 次 → 熔断，
        # 冷却期内只跑 L1/L3 micro，禁止再砍对话，防止压缩风暴把上下文打到不可用。
        self.thrash_max_events = int(_cfg("context_thrash_max_events", 3) or 3)
        self.thrash_window_sec = float(_cfg("context_thrash_window_sec", 180) or 180)
        self.thrash_cooldown_sec = float(_cfg("context_thrash_cooldown_sec", 300) or 300)
        self._l5_events: list[float] = []  # L5 触发时间戳（滑动窗口）
        self._thrash_until: float = 0.0    # 熔断截止 monotonic 时间
        self.meter = TokenMeter(
            context_window=self.context_length,
            threshold_percent=self.threshold_percent,
        )
        self.compression_count = 0
        self.last_layers: list[str] = []

    @property
    def name(self) -> str:
        return "pipeline"

    def update_from_response(self, usage: dict[str, Any] | None) -> None:
        self.meter.update_from_response(usage)
        self.last_prompt_tokens = self.meter.last_prompt_tokens
        self.last_completion_tokens = self.meter.last_completion_tokens
        self.last_total_tokens = self.meter.last_total_tokens
        # keep window in sync with runtime settings
        self.context_length = int(_cfg("context_window", self.context_length) or self.context_length)
        self.meter.context_window = self.context_length

    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        self.meter.context_window = int(
            _cfg("context_window", self.context_length) or self.context_length
        )
        self.meter.threshold_percent = float(
            _cfg("context_threshold_percent", self.threshold_percent) or self.threshold_percent
        )
        return self.meter.should_compress(prompt_tokens)

    def should_compress_preflight(self, messages: list[dict[str, Any]]) -> bool:
        est = self.meter.estimate_messages(messages)
        return self.meter.should_compress(est)

    # ── thrashing guard ─────────────────────────────────────────────

    def _thrash_active(self) -> bool:
        """是否处于熔断冷却期（只 micro，禁 L5）。"""
        return time.monotonic() < self._thrash_until

    def _record_l5_and_maybe_trip(self) -> None:
        """记录一次 L5 触发；滑动窗口内超限则进入熔断冷却。"""
        now = time.monotonic()
        self._l5_events = [t for t in self._l5_events if now - t <= self.thrash_window_sec]
        self._l5_events.append(now)
        if len(self._l5_events) >= self.thrash_max_events:
            self._thrash_until = now + self.thrash_cooldown_sec
            # 触发熔断后清空窗口，避免冷却刚结束就立刻再次熔断
            self._l5_events = []
            logger.warning(
                "Context thrashing detected: L5 x%d in %.0fs — entering cooldown %.0fs (micro-only)",
                self.thrash_max_events,
                self.thrash_window_sec,
                self.thrash_cooldown_sec,
            )

    def get_status(self) -> dict[str, Any]:
        base = super().get_status()
        base.update(self.meter.get_status())
        base["last_layers"] = list(self.last_layers)
        base["enable_l1"] = self.enable_l1
        base["enable_l3"] = self.enable_l3
        base["enable_l5"] = self.enable_l5
        return base

    async def compress(
        self,
        messages: list[dict[str, Any]],
        *,
        current_tokens: int | None = None,
        focus_topic: str | None = None,
        session_id: Any = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        # 同步 meter 参数，确保使用最新的 runtime settings
        self.meter.context_window = int(
            _cfg("context_window", self.context_length) or self.context_length
        )
        self.meter.threshold_percent = float(
            _cfg("context_threshold_percent", self.threshold_percent) or self.threshold_percent
        )
        layers: list[str] = []
        before = current_tokens or self.meter.estimate_messages(messages)
        meta: dict[str, Any] = {
            "compressed": False,
            "tokens_before": before,
            "layers": layers,
            "engine": self.name,
        }

        out = [dict(m) for m in messages]

        if self.enable_l1:
            out, n = self._l1_budget(out)
            if n:
                layers.append(f"L1:{n}")

        if self.enable_l3:
            out, n = self._l3_microcompact(out)
            if n:
                layers.append(f"L3:{n}")

        mid_tokens = self.meter.estimate_messages(out)
        thrashing = self._thrash_active()
        need_l5 = self.enable_l5 and (
            mid_tokens >= self.meter.threshold_tokens or self.should_compress(mid_tokens)
        )
        if thrashing and need_l5:
            # 熔断冷却期：禁止 L5 砍对话，只保留 L1/L3 micro，等冷却或手动干预
            logger.warning(
                "L5 suppressed by thrashing guard (cooldown %.0fs remaining)",
                self._thrash_until - time.monotonic(),
            )
            meta["thrash_suppressed_l5"] = True
            need_l5 = False

        if need_l5 and len(out) >= 4:
            self._record_l5_and_maybe_trip()
            out, l5_meta = await self._l5_auto_compact(
                out, focus_topic=focus_topic, session_id=session_id
            )
            if l5_meta.get("applied"):
                layers.append("L5")
                meta.update(l5_meta)

        after = self.meter.estimate_messages(out)
        meta["tokens_after"] = after
        meta["layers"] = layers
        meta["compressed"] = after < before or bool(layers)
        if layers:
            self.compression_count += 1
        self.last_layers = layers
        self.last_prompt_tokens = after
        self.meter.last_prompt_tokens = after
        logger.info(
            "Context pipeline: %s → %s tokens layers=%s",
            before,
            after,
            layers,
        )
        return out, meta

    # ── L1 ──────────────────────────────────────────────────────────

    def _l1_budget(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        limit = self.max_tool_output_chars
        changed = 0
        out: list[dict[str, Any]] = []
        for m in messages:
            role = m.get("role")
            content = m.get("content")
            if role == "tool" and isinstance(content, str) and len(content) > limit:
                # never use content[-0:] (that is the full string in Python)
                keep_head = max(32, min(limit // 2, limit - 32))
                keep_tail = max(32, limit - keep_head)
                omitted = max(0, len(content) - keep_head - keep_tail)
                m = {
                    **m,
                    "content": (
                        content[:keep_head]
                        + f"\n…[truncated {omitted} chars by L1 budget]…\n"
                        + content[-keep_tail:]
                    ),
                }
                changed += 1
            elif role == "assistant" and m.get("tool_calls"):
                tcs = []
                for tc in m["tool_calls"]:
                    if not isinstance(tc, dict):
                        tcs.append(tc)
                        continue
                    tc2 = dict(tc)
                    fn = dict(tc2.get("function") or {})
                    args = fn.get("arguments") or ""
                    if isinstance(args, str) and len(args) > limit:
                        fn["arguments"] = args[:limit] + "…[L1 truncated]"
                        tc2["function"] = fn
                        changed += 1
                    tcs.append(tc2)
                m = {**m, "tool_calls": tcs}
            out.append(m)
        return out, changed

    # ── L3 ──────────────────────────────────────────────────────────

    def _l3_microcompact(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], int]:
        """Collapse runs of tool results beyond protect window into one note."""
        systems = [m for m in messages if m.get("role") == "system"]
        rest = [m for m in messages if m.get("role") != "system"]
        if len(rest) <= self.protect_first_n + self.protect_last_n + 2:
            return messages, 0

        head = rest[: self.protect_first_n]
        mid = rest[self.protect_first_n : -self.protect_last_n]
        tail = rest[-self.protect_last_n :]
        if not mid:
            return messages, 0

        tool_n = sum(1 for m in mid if m.get("role") == "tool")
        if tool_n < 4:
            return messages, 0

        # Keep non-tool structure lightly: drop pure tool rows in mid, keep user/assistant
        kept_mid: list[dict[str, Any]] = []
        dropped_tools = 0
        # 记录被剥掉 tool_calls 的 tool_call_id：它们的 tool 结果消息若落在
        # head/tail 保护区，会变成孤儿（assistant.tool_calls 已丢失），必须一并剔除，
        # 否则严格 OpenAI 兼容网关（如 Kimi）会以 400 拒绝。
        stripped_tc_ids: set[str] = set()
        for m in mid:
            if m.get("role") == "tool":
                dropped_tools += 1
                continue
            # strip heavy tool_calls from mid assistants (keep text)
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    if isinstance(tc, dict) and tc.get("id"):
                        stripped_tc_ids.add(str(tc["id"]))
                content = m.get("content") or f"[tool calls omitted x{len(m['tool_calls'])}]"
                kept_mid.append({"role": "assistant", "content": content})
            else:
                kept_mid.append(m)

        if dropped_tools < 3:
            return messages, 0

        # 从保护区中剔除已成为孤儿的 tool 消息（其 tool_call_id 已被剥掉）
        def _drop_orphan_tools(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            if not stripped_tc_ids:
                return rows
            return [
                r
                for r in rows
                if not (
                    r.get("role") == "tool"
                    and r.get("tool_call_id")
                    and str(r["tool_call_id"]) in stripped_tc_ids
                )
            ]

        head = _drop_orphan_tools(head)
        tail = _drop_orphan_tools(tail)

        note = {
            "role": "system",
            "content": (
                f"[L3 microcompact] Omitted {dropped_tools} intermediate tool outputs "
                f"from older turns; recent tool results are kept in the tail."
            ),
        }
        return systems + head + [note] + kept_mid + tail, dropped_tools

    # ── L5 ──────────────────────────────────────────────────────────

    async def _l5_auto_compact(
        self,
        messages: list[dict[str, Any]],
        *,
        focus_topic: str | None,
        session_id: Any,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        systems = [m for m in messages if m.get("role") == "system"]
        # Keep first system as stable identity if present; extra systems go into compress body
        stable_systems = systems[:1] if systems else []
        extra_systems = systems[1:]
        rest = extra_systems + [m for m in messages if m.get("role") != "system"]

        if len(rest) < 6:
            return messages, {"applied": False}

        keep_tail = max(6, self.protect_last_n)
        head = rest[:-keep_tail]
        tail = rest[-keep_tail:]
        if not head:
            return messages, {"applied": False}

        lines: list[str] = []
        for m in head:
            role = m.get("role", "?")
            content = m.get("content") or ""
            if isinstance(content, str) and content.strip():
                lines.append(f"{role}: {content[:2000]}")
            tcs = m.get("tool_calls")
            if tcs:
                names = []
                for tc in tcs:
                    if isinstance(tc, dict):
                        names.append((tc.get("function") or {}).get("name") or "tool")
                if names:
                    lines.append(f"{role}: [tool_calls: {', '.join(names)}]")
        transcript = "\n".join(lines)
        if len(transcript) > 40_000:
            transcript = transcript[:40_000] + "\n…[truncated]"

        focus_line = f"\nFocus topic: {focus_topic}" if focus_topic else ""
        summary_text = await self._llm_summarize(transcript, focus_line)

        if not summary_text:
            summary_text = f"[历史已压缩：省略较早 {len(head)} 条消息]"

        # optional CtxItem
        if session_id is not None:
            try:
                from backend.repositories.context_repo import AsyncCtxItemRepository

                repo = AsyncCtxItemRepository()
                await repo.create(
                    {
                        "session_id": session_id,
                        "scope": "session",
                        "kind": "memory",
                        "key": f"summary_{int(uuid.uuid4().int % 1e12)}",
                        "value": summary_text,
                        "tokens": max(8, round(len(summary_text) / 3.4)),
                        "pinned": True,
                        "ttl": "session",
                        "origin": "context_pipeline_l5",
                    }
                )
            except Exception as e:
                logger.debug("save summary ctx failed: %s", e)

        body = (
            f"{SUMMARY_PREFIX}\n\n"
            f"## Historical Task Snapshot\n{summary_text}\n\n"
            f"{_SUMMARY_END}"
        )
        summary_msg = {
            "role": "system",
            "content": body,
            "_compressed_summary": True,
        }
        # 压缩后 tail 中可能残留"孤儿 tool 消息"：其 tool_call_id 对应的
        # assistant.tool_calls 落在被压缩的 head 区段，已随摘要消失。
        # 统一收集新序列中仍存在的 tool_call_id，剔除不匹配的 tool 消息，
        # 避免严格 OpenAI 兼容网关（如 Kimi）以 400 拒绝。
        new_messages = stable_systems + [summary_msg] + tail
        live_tc_ids: set[str] = set()
        for m in new_messages:
            if m.get("role") == "assistant":
                for tc in m.get("tool_calls") or []:
                    if isinstance(tc, dict) and tc.get("id"):
                        live_tc_ids.add(str(tc["id"]))
        new_messages = [
            m
            for m in new_messages
            if not (
                m.get("role") == "tool"
                and m.get("tool_call_id")
                and str(m["tool_call_id"]) not in live_tc_ids
            )
        ]
        return new_messages, {
            "applied": True,
            "dropped_messages": len(head),
            "summary_chars": len(summary_text),
        }

    async def _llm_summarize(self, transcript: str, focus_line: str) -> str:
        try:
            llm = _get_compress_llm()
            prompt = [
                {
                    "role": "system",
                    "content": (
                        "你是会话压缩助手。将历史对话压缩为简洁中文要点。"
                        "使用小节：目标、已完成、关键事实、决策、未决问题、约束。"
                        "标注这些是历史状态，不是当前指令。不要编造。"
                        "输出纯文本，200-800 字。"
                        f"{focus_line}"
                    ),
                },
                {"role": "user", "content": transcript},
            ]
            parts: list[str] = []
            async for chunk in llm.chat(prompt, tools=None, stream=False):
                if getattr(chunk, "delta", None):
                    parts.append(chunk.delta)
            return "".join(parts).strip()
        except Exception as e:
            logger.warning("L5 LLM compress failed: %s", e)
            return ""


def _get_compress_llm():
    """Main model by default; optional override via settings.context_compress_model."""
    from backend.core.config import (
        AnthropicConfig,
        OllamaConfig,
        OpenAICompatibleConfig,
        OpenAIConfig,
        VLLMConfig,
        settings,
    )
    from backend.services.llm.anthropic import AnthropicService
    from backend.services.llm.factory import LLMServiceFactory
    from backend.services.llm.ollama import OllamaService
    from backend.services.llm.openai_cloud import OpenAIService
    from backend.services.llm.openai_compatible import OpenAICompatibleService
    from backend.services.llm.vllm import VLLMService

    override = (getattr(settings, "context_compress_model", None) or "").strip()
    if not override or override == (settings.llm_model or "").strip():
        return LLMServiceFactory.get_service()

    provider = settings.llm_provider
    base = settings.get_llm_config()
    # clone-ish config with model override
    data = {
        "base_url": base.base_url,
        "model": override,
        "max_tokens": min(getattr(base, "max_tokens", 4096) or 4096, 4096),
        "temperature": 0.2,
        "api_key": getattr(base, "api_key", None),
    }
    if provider == "ollama":
        return OllamaService(OllamaConfig(**data))
    if provider == "vllm":
        return VLLMService(VLLMConfig(**data))
    if provider == "openai":
        return OpenAIService(OpenAIConfig(**data))
    if provider == "anthropic":
        return AnthropicService(AnthropicConfig(**data))
    return OpenAICompatibleService(OpenAICompatibleConfig(**data))
