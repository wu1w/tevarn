"""Anthropic-strict context compression + tool microcompact.

Guarantees for any message list leaving this module (when ensure_strict=True):
1. Every assistant.tool_calls block is immediately followed by tool results
   covering ALL call ids (OpenAI + Anthropic Messages pairing rule).
2. No orphan role=tool without a preceding unmatched tool_call id.
3. Microcompact clears old tool *content* but never deletes the pair structure.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Claude-style placeholder (pair kept, payload gone)
CLEARED_TOOL_RESULT = "[Old tool result content cleared]"


def estimate_text(text: str) -> int:
    if not text:
        return 0
    chinese = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - chinese
    return max(1, int(chinese / 1.5 + other / 4.0) + 1)


def estimate_messages(messages: list[dict[str, Any]]) -> int:
    total = 0
    for msg in messages:
        total += 6
        c = msg.get("content")
        if isinstance(c, str):
            total += estimate_text(c)
        elif c is not None:
            total += estimate_text(json.dumps(c, ensure_ascii=False))
        tcs = msg.get("tool_calls")
        if tcs:
            total += estimate_text(json.dumps(tcs, ensure_ascii=False))
    return total


def tool_call_ids(msg: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for tc in msg.get("tool_calls") or []:
        tid = tc.get("id")
        if tid:
            out.append(str(tid))
    return out


def validate_tool_integrity(messages: list[dict[str, Any]]) -> list[str]:
    """Anthropic/OpenAI strict pairing errors (empty list = OK)."""
    errors: list[str] = []
    pending: dict[str, int] = {}

    for i, m in enumerate(messages):
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            if pending:
                errors.append(
                    f"msg[{i}] assistant tool_calls while previous unresolved: {list(pending)}"
                )
                pending.clear()
            ids = tool_call_ids(m)
            if not ids:
                errors.append(f"msg[{i}] assistant tool_calls missing ids")
            for tid in ids:
                pending[tid] = i
            continue

        if role == "tool":
            tid = str(m.get("tool_call_id") or "")
            if not tid:
                errors.append(f"msg[{i}] tool missing tool_call_id")
            elif tid not in pending:
                errors.append(f"msg[{i}] orphan tool result id={tid[:24]}")
            else:
                del pending[tid]
            continue

        if pending:
            errors.append(
                f"msg[{i}] role={role} before tool results completed: missing {list(pending)}"
            )
            pending.clear()

    if pending:
        errors.append(f"eof missing tool results for {list(pending)}")
    return errors


def is_context_overflow_error(exc: BaseException | str) -> bool:
    """Detect provider context-window / prompt-too-long errors for reactive compact."""
    s = str(exc).lower()
    needles = (
        "context_length",
        "context length",
        "context window",
        "maximum context",
        "max context",
        "prompt is too long",
        "prompt too long",
        "too many tokens",
        "token limit",
        "context_length_exceeded",
        "model_context_window_exceeded",
        "exceeds model context",
        "exceed context",
        "input is too long",
        "request too large",
        "payload too large",
        "reduce the length",
        "maximum prompt length",
    )
    return any(n in s for n in needles)


@dataclass
class CompressEvent:
    ts: float
    before_tokens: int
    after_tokens: int
    dropped_messages: int
    reason: str


@dataclass
class TokenMeter:
    context_window: int = 65536
    threshold_percent: float = 0.55

    @property
    def threshold_tokens(self) -> int:
        return int(self.context_window * self.threshold_percent)

    @property
    def micro_threshold_tokens(self) -> int:
        """Soft threshold: start pruning tool payloads earlier (~70% of hard threshold)."""
        return int(self.threshold_tokens * 0.7)

    def should_compress(self, messages: list[dict[str, Any]], threshold_ratio: float | None = None) -> bool:
        ratio = self.threshold_percent if threshold_ratio is None else threshold_ratio
        return estimate_messages(messages) >= int(self.context_window * ratio)

    def should_microcompact(self, messages: list[dict[str, Any]]) -> bool:
        return estimate_messages(messages) >= self.micro_threshold_tokens

    def status(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        used = estimate_messages(messages)
        return {
            "context_window": self.context_window,
            "threshold_percent": self.threshold_percent,
            "threshold_tokens": self.threshold_tokens,
            "micro_threshold_tokens": self.micro_threshold_tokens,
            "used_tokens": used,
            "usage_ratio": round(used / max(1, self.context_window), 4),
        }


def _find_complete_tool_blocks(messages: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """Return (start, end) half-open ranges for complete assistant→tools blocks."""
    blocks: list[tuple[int, int]] = []
    i = 0
    n = len(messages)
    while i < n:
        m = messages[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            ids = tool_call_ids(m)
            j = i + 1
            got: set[str] = set()
            while j < n and messages[j].get("role") == "tool":
                tid = str(messages[j].get("tool_call_id") or "")
                if tid:
                    got.add(tid)
                j += 1
            if ids and all(tid in got for tid in ids):
                blocks.append((i, j))
                i = j
                continue
        i += 1
    return blocks


def _offload_text(text: str, offload_dir: Path | None, tool_call_id: str) -> str | None:
    if not offload_dir or not text:
        return None
    try:
        offload_dir.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha1(tool_call_id.encode("utf-8", errors="replace")).hexdigest()[:12]
        path = offload_dir / f"tool_{h}.txt"
        path.write_text(text, encoding="utf-8", errors="replace")
        return str(path)
    except Exception:
        return None


def microcompact_tools(
    messages: list[dict[str, Any]],
    *,
    keep_recent_blocks: int = 4,
    max_tool_chars: int = 4000,
    offload_dir: Path | None = None,
    clear_all_but_recent: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Prune old tool *payloads* while keeping Anthropic-strict pairs.

    - Complete tool blocks older than keep_recent_blocks: content → CLEARED (+ disk path).
    - Recent blocks: truncate if > max_tool_chars (offload full text).
    - Structure (assistant.tool_calls + tool rows + ids) is never removed.
    """
    if not messages:
        return messages, {"cleared_blocks": 0, "trimmed_tools": 0}

    out = [dict(m) for m in messages]
    blocks = _find_complete_tool_blocks(out)
    stats = {"cleared_blocks": 0, "trimmed_tools": 0, "blocks_total": len(blocks)}

    if not blocks:
        # still trim oversized tool rows that are not in complete blocks
        for i, m in enumerate(out):
            if m.get("role") == "tool" and isinstance(m.get("content"), str):
                c = m["content"]
                if len(c) > max_tool_chars and CLEARED_TOOL_RESULT not in c[:80]:
                    path = _offload_text(c, offload_dir, str(m.get("tool_call_id") or i))
                    note = f"{CLEARED_TOOL_RESULT}"
                    if path:
                        note += f"\n[full output: {path}]"
                    else:
                        note += f"\n…[trimmed {len(c) - max_tool_chars} chars]"
                        note = c[:max_tool_chars] + "\n" + note
                    out[i] = {**m, "content": note if path else (c[:max_tool_chars] + f"\n…[trimmed {len(c)-max_tool_chars} chars]")}
                    stats["trimmed_tools"] += 1
        return out, stats

    keep_n = max(1, keep_recent_blocks)
    clear_upto = 0 if clear_all_but_recent else max(0, len(blocks) - keep_n)
    clear_set = set(range(0, clear_upto))
    recent_set = set(range(clear_upto, len(blocks)))

    for bi, (start, end) in enumerate(blocks):
        for j in range(start + 1, end):
            m = out[j]
            if m.get("role") != "tool":
                continue
            content = m.get("content")
            if not isinstance(content, str):
                continue
            if content.strip().startswith(CLEARED_TOOL_RESULT):
                continue
            tid = str(m.get("tool_call_id") or f"idx{j}")

            if bi in clear_set:
                path = _offload_text(content, offload_dir, tid)
                note = CLEARED_TOOL_RESULT
                if path:
                    note += f"\n[full output: {path}]"
                name = m.get("name") or ""
                if name:
                    note = f"{note}\n[tool={name}]"
                out[j] = {**m, "content": note}
                stats["cleared_blocks"] += 1  # count tools cleared
            elif bi in recent_set and len(content) > max_tool_chars:
                path = _offload_text(content, offload_dir, tid)
                head = content[:max_tool_chars]
                tail = f"\n…[trimmed {len(content) - max_tool_chars} chars]"
                if path:
                    tail += f"\n[full output: {path}]"
                out[j] = {**m, "content": head + tail}
                stats["trimmed_tools"] += 1

        # shrink huge tool_call arguments on cleared/old assistants slightly
        if bi in clear_set:
            asst = out[start]
            if asst.get("tool_calls"):
                tcs = []
                for tc in asst["tool_calls"]:
                    tc2 = dict(tc)
                    fn = dict(tc2.get("function") or {})
                    args = fn.get("arguments") or ""
                    if isinstance(args, str) and len(args) > 2000:
                        fn["arguments"] = args[:2000] + "…"
                    tc2["function"] = fn
                    tcs.append(tc2)
                out[start] = {**asst, "tool_calls": tcs}

    return out, stats


def ensure_anthropic_strict(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Repair to a pair-safe list. Prefer dropping incomplete tool blocks over 400s."""
    return _repair_tool_integrity(messages)


def _repair_tool_integrity(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not messages:
        return messages
    out: list[dict[str, Any]] = []
    i = 0
    n = len(messages)
    while i < n:
        m = messages[i]
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            ids = tool_call_ids(m)
            j = i + 1
            got: dict[str, dict[str, Any]] = {}
            order: list[str] = []
            while j < n and messages[j].get("role") == "tool":
                tid = str(messages[j].get("tool_call_id") or "")
                if tid and tid not in got:
                    got[tid] = messages[j]
                    order.append(tid)
                elif tid:
                    got[tid] = messages[j]
                j += 1
            if ids and all(tid in got for tid in ids):
                # keep assistant; emit tools in tool_calls id order (Anthropic-friendly)
                asst = dict(m)
                # empty content must be null for some gateways
                if asst.get("content") == "":
                    asst["content"] = None
                out.append(asst)
                for tid in ids:
                    tm = dict(got[tid])
                    if tm.get("content") is None:
                        tm["content"] = ""
                    out.append(tm)
                i = j
                continue
            # incomplete → strip tool_calls to plain assistant (never leave half pair)
            mc = {k: v for k, v in m.items() if k != "tool_calls"}
            names = []
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                names.append(fn.get("name") or "tool")
            note = f"(tool_calls dropped for API integrity: {', '.join(names)})"
            prev = mc.get("content")
            if isinstance(prev, str) and prev.strip():
                mc["content"] = prev + "\n" + note
            else:
                mc["content"] = note
            out.append(mc)
            i = j
            continue

        if role == "tool":
            # orphan
            i += 1
            continue

        out.append(dict(m))
        i += 1

    # final safety
    errs = validate_tool_integrity(out)
    if errs:
        # nuclear: drop all tool structure, keep text only
        plain: list[dict[str, Any]] = []
        for m in out:
            if m.get("role") == "tool":
                continue
            if m.get("role") == "assistant" and m.get("tool_calls"):
                mc = {k: v for k, v in m.items() if k != "tool_calls"}
                mc["content"] = (mc.get("content") or "") or "(tools stripped)"
                plain.append(mc)
            else:
                plain.append(m)
        return plain
    return out


@dataclass
class ContextCompressor:
    meter: TokenMeter
    keep_recent: int = 8
    keep_recent_tool_blocks: int = 4
    max_tool_chars: int = 4000
    offload_dir: Path | None = None
    # static retain: prefer larger live window; archive full drops to disk
    compact_mode: str = "static"  # static | balanced | aggressive
    retain_turns: int = 24
    archive_dir: Path | None = None
    session_id: str = ""
    block_middle: bool = False  # thrashing: micro only
    rag_snippet: str = ""  # optional Desktop RAG text injected into summary
    events: list[CompressEvent] = field(default_factory=list)
    last_archive_path: str | None = None

    @property
    def compress_count(self) -> int:
        return len(self.events)

    def _effective_keep_recent(self) -> int:
        if self.compact_mode == "static":
            # keep more non-system messages in-window (dumb full retain)
            return max(self.keep_recent, min(80, self.retain_turns * 3))
        if self.compact_mode == "aggressive":
            return max(2, self.keep_recent // 2)
        return self.keep_recent

    def compress(
        self,
        messages: list[dict[str, Any]],
        *,
        force: bool = False,
        reason: str = "threshold",
        aggressive_tools: bool = False,
        block_middle: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Layered compress, Anthropic-strict safe end-to-end.

        1) microcompact tool payloads (pairs kept)
        2) if still over threshold and not thrashing-blocked: archive full middle → text summary
        3) ensure_anthropic_strict
        """
        before = estimate_messages(messages)
        keep_blocks = 2 if aggressive_tools or reason == "api_overflow" else self.keep_recent_tool_blocks
        no_middle = self.block_middle if block_middle is None else block_middle

        need_hard = force or before >= self.meter.threshold_tokens
        need_micro = force or before >= self.meter.micro_threshold_tokens or aggressive_tools

        if not need_micro and not need_hard:
            return ensure_anthropic_strict(messages)

        cur, mc_stats = microcompact_tools(
            messages,
            keep_recent_blocks=keep_blocks,
            max_tool_chars=self.max_tool_chars if not aggressive_tools else min(1500, self.max_tool_chars),
            offload_dir=self.offload_dir,
            clear_all_but_recent=aggressive_tools or reason == "api_overflow",
        )
        cur = ensure_anthropic_strict(cur)
        after_micro = estimate_messages(cur)

        if mc_stats.get("cleared_blocks") or mc_stats.get("trimmed_tools"):
            if after_micro < before or force:
                self.events.append(
                    CompressEvent(
                        time.time(),
                        before,
                        after_micro,
                        int(mc_stats.get("cleared_blocks") or 0),
                        reason + "+microcompact",
                    )
                )

        if not need_hard and after_micro < self.meter.threshold_tokens:
            return cur

        if after_micro < self.meter.threshold_tokens and not force:
            return cur

        # Thrashing / static soft-block: stay on microcompact only
        if no_middle and not force and reason != "api_overflow":
            return cur

        # Hard path: archive full middle then summary
        out = self._drop_middle_summary(cur, reason=reason)
        out = ensure_anthropic_strict(out)
        after = estimate_messages(out)

        if after >= self.meter.threshold_tokens or force or reason == "api_overflow":
            out, _ = microcompact_tools(
                out,
                keep_recent_blocks=max(1, keep_blocks // 2 or 1),
                max_tool_chars=min(1200, self.max_tool_chars),
                offload_dir=self.offload_dir,
                clear_all_but_recent=True,
            )
            out = ensure_anthropic_strict(out)
            after = estimate_messages(out)

        if after >= before and not force:
            return cur if validate_tool_integrity(cur) == [] else out

        self.events.append(
            CompressEvent(
                ts=time.time(),
                before_tokens=after_micro,
                after_tokens=after,
                dropped_messages=max(0, len(cur) - len(out)),
                reason=reason + "+middle",
            )
        )
        assert validate_tool_integrity(out) == [], validate_tool_integrity(out)
        return out

    def _archive_middle(self, middle: list[dict[str, Any]], *, reason: str) -> str | None:
        if not middle or not self.archive_dir:
            return None
        try:
            from takton_code.context.policy import ArchiveRetain

            ar = ArchiveRetain(
                root=Path(self.archive_dir),
                retain_turns=self.retain_turns,
                session_id=self.session_id or "default",
            )
            p = ar.append_messages(middle, note=f"compact:{reason}")
            self.last_archive_path = str(p)
            return str(p)
        except Exception:
            return None

    def _drop_middle_summary(
        self, messages: list[dict[str, Any]], *, reason: str
    ) -> list[dict[str, Any]]:
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        keep_n = max(2, self._effective_keep_recent())
        # emergency: if still far over threshold, collapse static fat window
        est = estimate_messages(system_msgs + non_system)
        if est >= self.meter.threshold_tokens * 1.5 or reason in ("api_overflow", "manual"):
            keep_n = max(2, min(keep_n, max(4, self.keep_recent)))
        if len(non_system) <= keep_n + 2:
            trimmed = system_msgs + self._trim_tool_payloads(non_system)
            return ensure_anthropic_strict(trimmed)

        head_keep = 1 if non_system and non_system[0].get("role") == "user" else 0

        start_idx = max(0, len(non_system) - keep_n)
        start_idx = self._align_start_index(non_system, start_idx)
        recent = non_system[start_idx:]
        middle = non_system[head_keep:start_idx]
        first = non_system[:head_keep] if head_keep else []
        middle, recent = self._rebalance_open_tools(middle, recent)

        # FULL archive before summarizing (static dumb retain)
        arch = self._archive_middle(middle, reason=reason)

        middle = ensure_anthropic_strict(middle)
        summary = self._summarize_middle(middle, archive_path=arch)
        recent = ensure_anthropic_strict(recent)
        out = system_msgs + first + summary + recent
        return out

    def _summarize_middle(
        self,
        middle: list[dict[str, Any]],
        *,
        archive_path: str | None = None,
    ) -> list[dict[str, Any]]:
        if not middle:
            return []
        lines: list[str] = []
        tool_names: list[str] = []
        for m in middle:
            role = m.get("role", "?")
            content = m.get("content") or ""
            if isinstance(content, str):
                snippet = content.replace("\n", " ")[:160]
            else:
                snippet = str(content)[:160]
            if m.get("tool_calls"):
                names = []
                for tc in m["tool_calls"]:
                    fn = tc.get("function") or {}
                    n = fn.get("name") or "tool"
                    names.append(n)
                    tool_names.append(n)
                snippet = f"tool_calls={','.join(names)} " + snippet
            if role == "tool":
                tname = m.get("name") or ""
                tid = m.get("tool_call_id") or ""
                lines.append(f"[tool:{tname or tid[:8]}] {snippet}")
            else:
                lines.append(f"[{role}] {snippet}")

        body = "\n".join(lines[-50:])
        tools_note = ""
        if tool_names:
            seen: set[str] = set()
            uniq = []
            for n in tool_names:
                if n not in seen:
                    seen.add(n)
                    uniq.append(n)
            tools_note = f"Tools used in dropped span: {', '.join(uniq[:40])}\n"

        prior = ""
        for m in middle:
            c = m.get("content") or ""
            if isinstance(c, str) and "[CONTEXT_COMPRESSED]" in c:
                prior = c
                break

        arch_note = f"Full transcript archived at: {archive_path}\n" if archive_path else ""
        rag_note = (self.rag_snippet + "\n") if self.rag_snippet else ""

        summary_text = (
            "[CONTEXT_COMPRESSED]\n"
            f"Dropped {len(middle)} messages. Continuity summary:\n"
            f"{arch_note}"
            f"{tools_note}"
            f"{body}\n"
        )
        if prior:
            summary_text += "\n[PRIOR_COMPRESSED_INCLUDED]\n" + prior[:3000] + "\n"
        if rag_note:
            summary_text += "\n" + rag_note
        summary_text += "[/CONTEXT_COMPRESSED]"

        return [
            {
                "role": "user",
                "content": (
                    "System note: earlier turns were compressed to save context. "
                    "A full local archive may be available at the path below. "
                    "Continue using the summary and recent messages. "
                    "Do not claim tool results not evidenced below or in recent turns. "
                    "Re-run tools when needed.\n\n" + summary_text
                ),
            },
            {
                "role": "assistant",
                "content": (
                    "Acknowledged compressed history. I will continue from the summary and "
                    "recent turns, re-invoke tools when prior results are not in context, "
                    "and treat any DESKTOP_RAG_CONTEXT as retrieved reference only."
                ),
            },
        ]

    def _align_start_index(self, non_system: list[dict[str, Any]], start_idx: int) -> int:
        idx = max(0, min(start_idx, len(non_system)))
        if idx >= len(non_system):
            return idx
        while idx > 0 and non_system[idx].get("role") == "tool":
            idx -= 1
        if idx < len(non_system) and non_system[idx].get("role") == "assistant" and non_system[
            idx
        ].get("tool_calls"):
            return idx
        if idx > 0:
            prev = non_system[idx - 1]
            if prev.get("role") == "assistant" and prev.get("tool_calls"):
                ids = set(tool_call_ids(prev))
                if idx < len(non_system) and non_system[idx].get("role") == "tool":
                    if str(non_system[idx].get("tool_call_id") or "") in ids:
                        return idx - 1
        return idx

    def _rebalance_open_tools(
        self, middle: list[dict[str, Any]], recent: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not middle:
            return middle, recent
        last_as_idx = None
        for i in range(len(middle) - 1, -1, -1):
            if middle[i].get("role") == "assistant" and middle[i].get("tool_calls"):
                last_as_idx = i
                break
        if last_as_idx is None:
            return middle, recent
        ids = set(tool_call_ids(middle[last_as_idx]))
        if not ids:
            return middle, recent
        have: set[str] = set()
        j = last_as_idx + 1
        while j < len(middle) and middle[j].get("role") == "tool":
            have.add(str(middle[j].get("tool_call_id") or ""))
            j += 1
        k = 0
        while k < len(recent) and recent[k].get("role") == "tool":
            have.add(str(recent[k].get("tool_call_id") or ""))
            k += 1
        if ids <= have:
            if k > 0 and not (
                ids <= {str(m.get("tool_call_id") or "") for m in middle[last_as_idx + 1 : j]}
            ):
                tail = middle[last_as_idx:]
                middle = middle[:last_as_idx]
                recent = tail + recent
            return middle, recent
        tail = middle[last_as_idx:]
        middle = middle[:last_as_idx]
        recent = tail + recent
        return middle, recent

    def _trim_tool_payloads(
        self, messages: list[dict[str, Any]], max_chars: int | None = None
    ) -> list[dict[str, Any]]:
        max_chars = max_chars or self.max_tool_chars
        out, _ = microcompact_tools(
            messages,
            keep_recent_blocks=9999,
            max_tool_chars=max_chars,
            offload_dir=self.offload_dir,
        )
        return out
