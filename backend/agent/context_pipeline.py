"""
Claude Code–inspired context pipeline:

  L1 Budget reduction — cap oversized tool / content blobs
  L3 Microcompact     — clear old tool *content*, keep tool_use/tool_result pairs
  L5 Auto-compact     — LLM structured summary for *continuing work*
                        (optional settings.context_compress_model)

Aligned with Claude Code leaked compact design (2026-03):
  - Compact inject message says CONTINUE, not "reference only / do not resume"
  - 9-section summary template (intent, files, errors, pending, current work, next step)
  - Microcompact preserves API tool pairs; only clears stale tool bodies
  - Mid-loop callers should pass allow_l5=False (L1/L3 only)

Hermes influences: protect head/tail, TokenMeter usage feedback.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any

from backend.agent.context_engine import ContextEngine
from backend.agent.token_meter import TokenMeter
from backend.core.config import settings

logger = logging.getLogger(__name__)

# Claude Code style: session continued — pick up the last task.
# Intentionally the OPPOSITE of "reference only / do not resume".
SUMMARY_PREFIX = (
    "This session is being continued from a previous conversation that ran out "
    "of context. The summary below covers the earlier portion of the conversation."
)

SUMMARY_CONTINUE = (
    "Continue the conversation from where it left off without asking the user any "
    "further questions. Resume directly — do not acknowledge the summary, do not "
    "recap what was happening, do not preface with \"I'll continue\" or similar. "
    "Pick up the last task as if the break never happened. Keep using tools until "
    "the work is actually complete."
)

# Back-compat alias used by tests / status probes
_SUMMARY_END = (
    "--- END OF CONTEXT SUMMARY — continue the work below from Current Work / Next Step ---"
)

CLEARED_TOOL_PLACEHOLDER = "[Old tool result content cleared]"

# Tools whose results are high-volume and usually re-fetchable (CC COMPACTABLE_TOOLS).
_COMPACTABLE_TOOL_HINTS = frozenset(
    {
        "file_read",
        "read",
        "read_file",
        "command",
        "bash",
        "shell",
        "python",
        "process",
        "grep",
        "glob",
        "search",
        "web_search",
        "web_fetch",
        "http",
        "browser",
        "doc_read",
        "file_write",
        "edit",
        "apply_patch",
        "write",
        "session_search",
    }
)

_MIN_CLEAR_CHARS = 120  # don't bother clearing tiny tool blobs


def _cfg(name: str, default: Any) -> Any:
    return getattr(settings, name, default)


def _repair_tool_pairs(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """去掉无匹配 tool_calls 的 tool 结果、无结果的悬空 tool_calls（公共修复）。"""
    if not messages:
        return messages
    # collect assistant tool_call ids
    call_ids: set[str] = set()
    for m in messages:
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            if isinstance(tc, dict) and tc.get("id"):
                call_ids.add(str(tc["id"]))
    out: list[dict[str, Any]] = []
    used_results: set[str] = set()
    for m in messages:
        if m.get("role") == "tool":
            tid = str(m.get("tool_call_id") or "")
            if tid and tid not in call_ids:
                continue  # orphan tool result
            if tid:
                used_results.add(tid)
            out.append(m)
            continue
        if m.get("role") == "assistant" and m.get("tool_calls"):
            tcs = [
                tc
                for tc in (m.get("tool_calls") or [])
                if isinstance(tc, dict)
            ]
            # 先原样保留；第二遍再剥无 result 的（需要完整扫描）
            out.append({**m, "tool_calls": tcs})
            continue
        out.append(m)
    # 第二遍：剥掉没有 tool 结果的 tool_calls（避免网关 400）
    result_ids = {
        str(m.get("tool_call_id") or "")
        for m in out
        if m.get("role") == "tool" and m.get("tool_call_id")
    }
    fixed: list[dict[str, Any]] = []
    for m in out:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            tcs = [
                tc
                for tc in m["tool_calls"]
                if str(tc.get("id") or "") in result_ids
                or not tc.get("id")  # 无 id 无法配，保留由下游 sanitize
            ]
            if tcs:
                fixed.append({**m, "tool_calls": tcs})
            elif (m.get("content") or "").strip():
                nm = {**m}
                nm.pop("tool_calls", None)
                fixed.append(nm)
            # 空 assistant+空 tool_calls → 丢弃
            continue
        fixed.append(m)
    return fixed


def format_compact_summary(raw: str) -> str:
    """Strip <analysis> scratchpad; unwrap <summary> (Claude Code formatCompactSummary)."""
    text = raw or ""
    text = re.sub(r"<analysis>[\s\S]*?</analysis>", "", text, flags=re.I)
    m = re.search(r"<summary>([\s\S]*?)</summary>", text, flags=re.I)
    if m:
        text = f"Summary:\n{(m.group(1) or '').strip()}"
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_compact_continuation_message(
    summary_text: str,
    *,
    recent_messages_preserved: bool = True,
) -> str:
    """User-role body after L5 — mirrors Claude Code getCompactUserSummaryMessage."""
    formatted = format_compact_summary(summary_text)
    parts = [
        SUMMARY_PREFIX,
        "",
        formatted,
        "",
        SUMMARY_CONTINUE,
    ]
    if recent_messages_preserved:
        parts.insert(-2, "Recent messages after this summary are preserved verbatim.")
        parts.insert(-2, "")
    return "\n".join(parts).strip()


class PipelineContextEngine(ContextEngine):
    def __init__(self, *, profile: Any | None = None) -> None:
        self.profile = profile
        family = None
        l1_default = 12_000
        thr_default = 0.72
        l5_default = True
        if profile is not None:
            family = getattr(profile, "family", None)
            l1_default = int(getattr(profile, "l1_tool_chars", l1_default) or l1_default)
            thr_default = float(getattr(profile, "l3_threshold_ratio", thr_default) or thr_default)
            l5_default = bool(getattr(profile, "l5_enabled_default", True))
            cw = int(getattr(profile, "default_context_window", 0) or 0)
        else:
            cw = 0

        self.context_length = int(_cfg("context_window", cw or 128_000) or (cw or 128_000))
        self.threshold_percent = float(
            _cfg("context_threshold_percent", thr_default) or thr_default
        )
        self.protect_first_n = int(_cfg("context_protect_first_n", 3) or 3)
        self.protect_last_n = int(_cfg("context_protect_last_n", 12) or 12)
        self.max_tool_output_chars = int(
            _cfg("context_max_tool_output_chars", None)
            or _cfg("max_tool_result_length", l1_default)
            or l1_default
        )
        self.enable_l1 = bool(_cfg("context_enable_l1", True))
        self.enable_l3 = bool(_cfg("context_enable_l3", True))
        self.enable_l5 = bool(_cfg("context_enable_l5", l5_default))
        # Thrashing guard：180s 内 L5(hard compact) 触发 >= max_events 次 → 熔断，
        # 冷却期内只跑 L1/L3 micro，禁止再砍对话，防止压缩风暴把上下文打到不可用。
        self.thrash_max_events = int(_cfg("context_thrash_max_events", 3) or 3)
        self.thrash_window_sec = float(_cfg("context_thrash_window_sec", 180) or 180)
        self.thrash_cooldown_sec = float(_cfg("context_thrash_cooldown_sec", 300) or 300)
        self._l5_events: list[float] = []  # L5 触发时间戳（滑动窗口）
        self._thrash_until: float = 0.0    # 熔断截止 monotonic 时间
        # L2-H2：单次 compress 调用内 L5 上限 + 超限硬截断，防止压缩递归/风暴
        self.max_l5_retries = int(_cfg("context_max_l5_retries", 3) or 3)
        self._l5_attempts_run = 0
        self.meter = TokenMeter(
            context_window=self.context_length,
            threshold_percent=self.threshold_percent,
            family=family,
            chars_per_token_latin=getattr(profile, "chars_per_token_latin", None) if profile else None,
            chars_per_token_cjk=getattr(profile, "chars_per_token_cjk", None) if profile else None,
        )
        self.compression_count = 0
        self.last_layers: list[str] = []

    def apply_profile(self, profile: Any) -> None:
        """Hot-swap provider profile (e.g. when session model changes)."""
        if profile is None:
            return
        self.profile = profile
        self.meter.family = getattr(profile, "family", self.meter.family)
        self.meter.chars_per_token_latin = float(
            getattr(profile, "chars_per_token_latin", self.meter.chars_per_token_latin)
        )
        self.meter.chars_per_token_cjk = float(
            getattr(profile, "chars_per_token_cjk", self.meter.chars_per_token_cjk)
        )
        # Prefer larger of settings vs profile for tool chars only if settings default
        try:
            if not _cfg("context_max_tool_output_chars", None):
                self.max_tool_output_chars = int(
                    getattr(profile, "l1_tool_chars", self.max_tool_output_chars)
                )
        except Exception:
            pass
        cw = int(getattr(profile, "default_context_window", 0) or 0)
        # 会话模型窗口优先于全局 128k 默认（32k 模型勿等到 92k 才压；1M 勿过度压）
        if cw > 0:
            forced = int(_cfg("context_window", 0) or 0)
            # 仅当显式配置了 context_window 且与 profile 冲突时才尊重 settings 覆盖
            if forced > 0 and forced != 128_000:
                self.context_length = forced
            else:
                self.context_length = cw
            self.meter.context_window = self.context_length
            self._window_from_profile = True

    @property
    def name(self) -> str:
        return "pipeline"

    def update_from_response(self, usage: dict[str, Any] | None) -> None:
        self.meter.update_from_response(usage)
        self.last_prompt_tokens = self.meter.last_prompt_tokens
        self.last_completion_tokens = self.meter.last_completion_tokens
        self.last_total_tokens = self.meter.last_total_tokens
        # 若已 apply_profile 绑定模型窗口，不再被全局 settings.context_window 冲掉
        if not getattr(self, "_window_from_profile", False):
            self.context_length = int(
                _cfg("context_window", self.context_length) or self.context_length
            )
            self.meter.context_window = self.context_length

    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        if not getattr(self, "_window_from_profile", False):
            self.meter.context_window = int(
                _cfg("context_window", self.context_length) or self.context_length
            )
        self.meter.threshold_percent = float(
            _cfg("context_threshold_percent", self.threshold_percent) or self.threshold_percent
        )
        return self.meter.should_compress(prompt_tokens)

    def on_session_reset(self) -> None:
        super().on_session_reset()
        # 每 run / 会话切换：L5 次数与 thrash 归零（防全局永久禁用）
        self._l5_attempts_run = 0
        self._l5_events = []
        self._thrash_until = 0.0

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
        allow_l5: bool = True,
        micro_only: bool = False,
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
            "allow_l5": allow_l5 and not micro_only,
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
        need_l5 = (
            self.enable_l5
            and allow_l5
            and not micro_only
            and (mid_tokens >= self.meter.threshold_tokens or self.should_compress(mid_tokens))
        )
        if thrashing and need_l5:
            # 熔断冷却期：禁止 L5 砍对话，只保留 L1/L3 micro，等冷却或手动干预
            logger.warning(
                "L5 suppressed by thrashing guard (cooldown %.0fs remaining)",
                self._thrash_until - time.monotonic(),
            )
            meta["thrash_suppressed_l5"] = True
            need_l5 = False
        if not allow_l5 or micro_only:
            meta["l5_skipped_midloop"] = not allow_l5 or micro_only

        if need_l5 and len(out) >= 4:
            if self._l5_attempts_run >= self.max_l5_retries:
                logger.warning(
                    "L5 skipped: max_l5_retries=%s exhausted — hard truncate",
                    self.max_l5_retries,
                )
                meta["l5_retries_exhausted"] = True
            else:
                self._l5_attempts_run += 1
                self._record_l5_and_maybe_trip()
                out, l5_meta = await self._l5_auto_compact(
                    out, focus_topic=focus_topic, session_id=session_id
                )
                if l5_meta.get("applied"):
                    layers.append("L5")
                    meta.update(l5_meta)

        after = self.meter.estimate_messages(out)
        # 压缩后仍超阈值：硬截断 head/tail（L2-H2 关账路径）
        if after >= self.meter.threshold_tokens and len(out) > (
            self.protect_first_n + self.protect_last_n + 1
        ):
            out, n_drop = self._hard_truncate(out)
            if n_drop:
                layers.append(f"HARD:{n_drop}")
                after = self.meter.estimate_messages(out)
                meta["hard_truncated"] = n_drop
                logger.warning(
                    "Context hard-truncated dropped %s mid messages (tokens %s → %s)",
                    n_drop,
                    meta.get("tokens_before", before),
                    after,
                )

        # 统一修复 tool 配对（压缩/硬截断后的不变量）
        out = _repair_tool_pairs(out)
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
            "Context pipeline: %s → %s tokens layers=%s allow_l5=%s",
            before,
            after,
            layers,
            allow_l5 and not micro_only,
        )
        return out, meta

    def _hard_truncate(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], int]:
        """保留头/尾，丢弃中间；**扩展边界以不切断 tool_call/tool 配对**。"""
        head_n = max(1, self.protect_first_n)
        tail_n = max(1, self.protect_last_n)
        if len(messages) <= head_n + tail_n:
            return messages, 0
        # 向后扩 head：若 head 末条是带 tool_calls 的 assistant，吞掉紧随的 tool 结果
        head_end = head_n
        while head_end < len(messages) - tail_n:
            prev = messages[head_end - 1] if head_end > 0 else None
            cur = messages[head_end]
            if (
                prev
                and prev.get("role") == "assistant"
                and prev.get("tool_calls")
                and cur.get("role") == "tool"
            ):
                head_end += 1
                continue
            break
        # 向前扩 tail：若 tail 首条是 tool，把前面的 assistant tool_calls 一并纳入
        tail_start = len(messages) - tail_n
        while tail_start > head_end:
            cur = messages[tail_start]
            prev = messages[tail_start - 1] if tail_start > 0 else None
            if (
                cur.get("role") == "tool"
                and prev
                and prev.get("role") == "assistant"
                and prev.get("tool_calls")
            ):
                tail_start -= 1
                continue
            break
        if tail_start <= head_end:
            return messages, 0
        head = messages[:head_end]
        tail = messages[tail_start:]
        dropped = tail_start - head_end
        # 清 orphan tool（保险）
        head = _repair_tool_pairs(head)
        tail = _repair_tool_pairs(tail)
        marker = {
            "role": "system",
            "content": (
                f"[context hard-truncated: dropped {dropped} messages "
                f"after max_l5_retries={self.max_l5_retries}]"
            ),
        }
        return head + [marker] + tail, dropped

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

    # ── L3 (Claude Code microcompact) ───────────────────────────────

    @staticmethod
    def _tool_name_for_result(
        messages: list[dict[str, Any]], tool_call_id: str | None
    ) -> str:
        if not tool_call_id:
            return ""
        tid = str(tool_call_id)
        for m in messages:
            if m.get("role") != "assistant":
                continue
            for tc in m.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                if str(tc.get("id") or "") != tid:
                    continue
                return str((tc.get("function") or {}).get("name") or "").lower()
        return ""

    def _is_compactable_tool(self, name: str) -> bool:
        if not name:
            return True  # unknown → still clear large bodies in mid zone
        n = name.lower().strip()
        if n in _COMPACTABLE_TOOL_HINTS:
            return True
        # partial match e.g. mcp__xxx__file_read
        return any(h in n for h in _COMPACTABLE_TOOL_HINTS)

    def _l3_microcompact(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], int]:
        """Clear old mid-window tool *bodies*; keep assistant tool_calls + tool rows.

        Claude Code microCompact: replace content with a short cleared marker so
        token cost drops without breaking OpenAI tool pairing invariants.
        """
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

        cleared = 0
        kept_mid: list[dict[str, Any]] = []
        full_for_lookup = rest  # name resolution across head/mid/tail

        for m in mid:
            if m.get("role") != "tool":
                kept_mid.append(m)
                continue

            content = m.get("content")
            if not isinstance(content, str):
                kept_mid.append(m)
                continue
            if CLEARED_TOOL_PLACEHOLDER in content and len(content) < 200:
                kept_mid.append(m)
                continue
            if len(content) < _MIN_CLEAR_CHARS:
                kept_mid.append(m)
                continue

            # Prefer compactable tools; still clear large mid-zone blobs of any tool
            # (mid is already outside protect head/tail — safe to reclaim tokens).
            tname = self._tool_name_for_result(full_for_lookup, m.get("tool_call_id"))
            if not self._is_compactable_tool(tname) and len(content) < 500:
                kept_mid.append(m)
                continue

            preview = content[:80].replace("\n", " ").strip()
            new_content = CLEARED_TOOL_PLACEHOLDER
            if preview:
                new_content += f" (was ~{len(content)} chars; preview: {preview}…)"
            kept_mid.append({**m, "content": new_content})
            cleared += 1

        if cleared < 3:
            return messages, 0

        # Structure intact → no orphan stripping needed (pairs preserved).
        return systems + head + kept_mid + tail, cleared

    # ── L5 ──────────────────────────────────────────────────────────

    def _build_transcript(self, head: list[dict[str, Any]]) -> str:
        """Richer transcript for summarizer: include tool names, args peek, result peek."""
        lines: list[str] = []
        for m in head:
            role = m.get("role", "?")
            content = m.get("content") or ""
            if isinstance(content, str) and content.strip():
                # tool bodies: keep more signal for errors/paths
                cap = 1500 if role == "tool" else 2500
                body = content if len(content) <= cap else content[:cap] + "…[truncated]"
                lines.append(f"{role}: {body}")
            tcs = m.get("tool_calls")
            if tcs:
                for tc in tcs:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function") or {}
                    name = fn.get("name") or "tool"
                    args = fn.get("arguments") or ""
                    if not isinstance(args, str):
                        args = str(args)
                    args_peek = args[:400] + ("…" if len(args) > 400 else "")
                    lines.append(f"{role}: [tool_call {name} id={tc.get('id')}] {args_peek}")
        transcript = "\n".join(lines)
        if len(transcript) > 60_000:
            transcript = transcript[:60_000] + "\n…[truncated]"
        return transcript

    async def _l5_auto_compact(
        self,
        messages: list[dict[str, Any]],
        *,
        focus_topic: str | None,
        session_id: Any,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        systems = [m for m in messages if m.get("role") == "system"]
        # 保留：首条 system + 短编排/能力纪律（防长任务后半程行为漂移）
        stable_systems: list[dict[str, Any]] = []
        extra_systems: list[dict[str, Any]] = []
        for i, m in enumerate(systems):
            c = m.get("content") if isinstance(m.get("content"), str) else ""
            keep = i == 0 or (
                len(c) <= 4000
                and any(
                    k in c.lower()
                    for k in (
                        "capability",
                        "能力",
                        "orchestr",
                        "编排",
                        "steward",
                        "crew",
                        "discipline",
                        "纪律",
                        "you are",
                        "你是",
                    )
                )
            )
            if keep and len(stable_systems) < 4:
                stable_systems.append(m)
            else:
                extra_systems.append(m)
        rest = extra_systems + [m for m in messages if m.get("role") != "system"]

        if len(rest) < 6:
            return messages, {"applied": False}

        keep_tail = max(6, self.protect_last_n)
        head = rest[:-keep_tail]
        tail = rest[-keep_tail:]
        if not head:
            return messages, {"applied": False}

        transcript = self._build_transcript(head)
        focus_line = f"\nFocus topic: {focus_topic}" if focus_topic else ""
        summary_text = await self._llm_summarize(transcript, focus_line)

        if not summary_text:
            # Heuristic fallback — still orientation for continuation, not a ban.
            user_bits = [
                (m.get("content") or "")[:200]
                for m in head
                if m.get("role") == "user" and isinstance(m.get("content"), str)
            ]
            summary_text = (
                "1. Primary Request and Intent:\n"
                f"   {user_bits[0] if user_bits else '(unknown)'}\n\n"
                f"7. Pending Tasks:\n   Continue unfinished work from earlier turns "
                f"(compressed {len(head)} messages).\n\n"
                "8. Current Work:\n   Context was compacted due to length; "
                "use recent messages after this summary as ground truth.\n\n"
                "9. Optional Next Step:\n   Resume the last incomplete task with tools."
            )

        summary_text = format_compact_summary(summary_text)

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

        body = build_compact_continuation_message(
            summary_text, recent_messages_preserved=True
        )
        # Claude Code injects compact summary as a *user* message so the model
        # treats it as session continuation, not a system "stop working" ban.
        summary_msg = {
            "role": "user",
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
        # Also strip orphan tool_calls on assistant rows in tail (no matching tool)
        live_tool_result_ids = {
            str(m.get("tool_call_id"))
            for m in new_messages
            if m.get("role") == "tool" and m.get("tool_call_id")
        }
        fixed: list[dict[str, Any]] = []
        for m in new_messages:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                kept_tc = [
                    tc
                    for tc in m["tool_calls"]
                    if isinstance(tc, dict)
                    and str(tc.get("id") or "") in live_tool_result_ids
                ]
                if len(kept_tc) != len(m["tool_calls"]):
                    mm = dict(m)
                    if kept_tc:
                        mm["tool_calls"] = kept_tc
                    else:
                        mm.pop("tool_calls", None)
                        if not (mm.get("content") or "").strip():
                            mm["content"] = "[tool calls compacted with history]"
                    fixed.append(mm)
                    continue
            fixed.append(m)
        new_messages = fixed

        return new_messages, {
            "applied": True,
            "dropped_messages": len(head),
            "summary_chars": len(summary_text),
            "continuation": True,
        }

    async def _llm_summarize(self, transcript: str, focus_line: str) -> str:
        try:
            llm = _get_compress_llm()
            # Claude Code BASE_COMPACT_PROMPT (9 sections) — bilingual OK, Chinese preferred.
            system = f"""CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.
You are a conversation compaction assistant. Create a detailed summary so the agent can CONTINUE development work without losing context. Do not invent facts.
{focus_line}

Your summary MUST include these sections (use Chinese if the conversation is Chinese):

1. Primary Request and Intent: Capture the user's explicit requests and intents in detail.
2. Key Technical Concepts: Important technologies, frameworks, patterns discussed.
3. Files and Code Sections: Specific files examined/modified/created; include important paths and short snippets when present; note why each matters.
4. Errors and Fixes: Errors encountered and how they were fixed; include user corrections.
5. Problem Solving: Solved problems and ongoing troubleshooting.
6. All User Messages: List ALL non-tool user messages (critical for intent tracking).
7. Pending Tasks: Explicit unfinished tasks still requested by the user.
8. Current Work: Precisely what was being worked on immediately before compaction (files, commands, last tool outcomes).
9. Optional Next Step: The next concrete step that is DIRECTLY in line with the user's most recent explicit requests and the work in progress. If the last task was concluded, only list next steps if the user asked. Include short verbatim quotes of where you left off.

Use this shape (optional tags):
<analysis>
brief private checklist
</analysis>
<summary>
1. Primary Request and Intent:
   ...
9. Optional Next Step:
   ...
</summary>

Be thorough on technical detail needed to continue coding (paths, errors, decisions). Prefer 800–4000 Chinese characters over a vague short blurb. Never say "do not resume" or "reference only"."""

            prompt = [
                {"role": "system", "content": system},
                {"role": "user", "content": transcript},
            ]
            parts: list[str] = []
            finish = None
            async for chunk in llm.chat(prompt, tools=None, stream=False):
                fr = getattr(chunk, "finish_reason", None)
                if fr:
                    finish = fr
                d = getattr(chunk, "delta", None)
                if d:
                    parts.append(d)
            text = "".join(parts).strip()
            # 非流式错误常被包成 delta="[LLM Error 400]…"——绝不可当摘要写入 pinned
            if finish in ("error", "content_filter"):
                logger.warning("L5 compress aborted finish_reason=%s", finish)
                return ""
            if not text:
                return ""
            low = text.lower()
            if (
                low.startswith("[llm error")
                or "error 400" in low
                or "error 401" in low
                or "error 429" in low
                or "error 500" in low
                or "invalid_api_key" in low
                or "context_length_exceeded" in low
            ):
                logger.warning("L5 compress got error payload, discard: %s", text[:160])
                return ""
            return text
        except Exception as e:
            logger.warning("L5 LLM compress failed: %s", e)
            return ""


def _get_compress_llm():
    """Main model by default; optional override via settings.context_compress_model.

    Never use reasoning/think models for L5 (completion cost explosion).
    """
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
    from backend.services.llm.provider_profiles import resolve_profile
    from backend.services.llm.vllm import VLLMService

    override = (getattr(settings, "context_compress_model", None) or "").strip()
    main_model = (settings.llm_model or "").strip()
    provider = settings.llm_provider
    base = settings.get_llm_config()
    profile = resolve_profile(
        base_url=getattr(base, "base_url", None),
        model=override or main_model,
        llm_provider=provider,
    )

    def _pick_model() -> str:
        cand = override or main_model
        if cand and profile.is_reasoning_model(cand):
            hint = (profile.recommended_compress_model_hint or "").strip()
            if hint and not profile.is_reasoning_model(hint):
                logger.info(
                    "L5 compress: skipping reasoning model %r → %r", cand, hint
                )
                return hint
            # last resort: strip common reasoner suffixes
            for bad in ("-reasoner", "-thinking", "-r1"):
                if cand.lower().endswith(bad):
                    alt = cand[: -len(bad)]
                    if alt:
                        return alt
            logger.warning(
                "L5 compress using reasoning model %r (no non-reasoner override)",
                cand,
            )
        return cand or main_model

    model = _pick_model()
    if not override or override == main_model:
        if model == main_model and not profile.is_reasoning_model(main_model):
            return LLMServiceFactory.get_service()
        # need override path even when settings.context_compress_model empty
        if model == main_model:
            return LLMServiceFactory.get_service()

    data = {
        "base_url": base.base_url,
        "model": model,
        "max_tokens": min(getattr(base, "max_tokens", 4096) or 4096, 4096),
        "temperature": 0.2,
        "api_key": getattr(base, "api_key", None),
    }
    if provider == "ollama":
        return OllamaService(OllamaConfig(**data))
    if provider == "vllm":
        return VLLMService(VLLMConfig(**data))
    if provider == "openai":
        return OpenAIService(OpenAIConfig(**data), profile=profile)
    if provider == "anthropic":
        return AnthropicService(AnthropicConfig(**data), profile=profile)
    return OpenAICompatibleService(OpenAICompatibleConfig(**data), profile=profile)
