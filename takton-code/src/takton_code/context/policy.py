"""Context policy: thrashing guard, dual meter, static archive retain, RAG assist."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from takton_code.context.compressor import estimate_messages


CompactMode = Literal["static", "balanced", "aggressive"]


def recommended_thrashing(context_window: int) -> dict[str, float | int]:
    """Calibrate thrashing guard to real context window (LOCAL small vs 64k+).

    Small windows (e.g. stress 12k) trigger hard compact often — defaults of
    3/180s would false-trip and freeze middle summary while context stays hot.
    """
    w = max(1, int(context_window or 65536))
    if w <= 16000:
        return {
            "max_events": 8,
            "window_sec": 60.0,
            "cooldown_sec": 90.0,
            "note": "small_window<=16k: tolerate frequent hard compact",
        }
    if w <= 48000:
        return {
            "max_events": 5,
            "window_sec": 120.0,
            "cooldown_sec": 180.0,
            "note": "mid_window<=48k",
        }
    return {
        "max_events": 3,
        "window_sec": 180.0,
        "cooldown_sec": 300.0,
        "note": "large_window>48k (default 64k+)",
    }


@dataclass
class ThrashingGuard:
    """Detect compact thrashing (many compacts in a short window).

    Claude Code warns when context refills within ~3 turns after compact, 3× in a row.
    We trip after ``max_events`` hard-compacts inside ``window_sec``.
    When tripped: prefer microcompact-only; block middle-summary until reset/cooldown.
    """

    max_events: int = 3
    window_sec: float = 180.0
    events: list[float] = field(default_factory=list)
    tripped_at: float | None = None
    cooldown_sec: float = 300.0

    def record(self, *, kind: str = "middle") -> bool:
        """Record a compact event. Returns True if thrashing is active after this."""
        if kind not in ("middle", "api_overflow", "manual_hard"):
            return self.active
        now = time.time()
        self.events = [t for t in self.events if now - t <= self.window_sec]
        self.events.append(now)
        if len(self.events) >= self.max_events:
            self.tripped_at = now
        return self.active

    @property
    def active(self) -> bool:
        if self.tripped_at is None:
            return False
        if time.time() - self.tripped_at > self.cooldown_sec:
            # auto cool down
            self.tripped_at = None
            self.events.clear()
            return False
        return True

    def reset(self) -> None:
        self.events.clear()
        self.tripped_at = None

    def status(self) -> dict[str, Any]:
        return {
            "thrashing": self.active,
            "events_in_window": len(self.events),
            "max_events": self.max_events,
            "window_sec": self.window_sec,
            "cooldown_sec": self.cooldown_sec,
            "tripped_at": self.tripped_at,
        }


@dataclass
class ArchiveRetain:
    """OpenClaw-style dumb retain: full transcript on disk; live window stays complete longer.

    When middle messages are dropped from the LLM window, the *full* JSON lines are
    appended under archives/<session>/transcript.jsonl so nothing is lost locally.
    """

    root: Path
    retain_turns: int = 24  # keep last N user turns fully in-window when possible
    session_id: str = ""

    def session_dir(self) -> Path:
        d = self.root / (self.session_id or "default")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def transcript_path(self) -> Path:
        return self.session_dir() / "transcript.jsonl"

    def append_messages(self, messages: list[dict[str, Any]], *, note: str = "") -> Path:
        p = self.transcript_path()
        ts = time.time()
        with p.open("a", encoding="utf-8") as f:
            if note:
                f.write(
                    json.dumps({"_meta": True, "ts": ts, "note": note}, ensure_ascii=False) + "\n"
                )
            for m in messages:
                row = {"ts": ts, "role": m.get("role"), "content": m.get("content")}
                if m.get("tool_calls"):
                    row["tool_calls"] = m["tool_calls"]
                if m.get("tool_call_id"):
                    row["tool_call_id"] = m["tool_call_id"]
                if m.get("name"):
                    row["name"] = m["name"]
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return p

    def tail_text(self, max_chars: int = 6000) -> str:
        p = self.transcript_path()
        if not p.is_file():
            return ""
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        if len(raw) <= max_chars:
            return raw
        return raw[-max_chars:]

    def count_user_turns(self, messages: list[dict[str, Any]]) -> int:
        return sum(1 for m in messages if m.get("role") == "user")


def build_context_meter(
    messages: list[dict[str, Any]],
    *,
    context_window: int,
    threshold_percent: float,
    usage_totals: dict[str, int] | None = None,
    compress_count: int = 0,
    thrashing: dict[str, Any] | None = None,
    mode: str = "static",
    archive_path: str | None = None,
) -> dict[str, Any]:
    """Dual meter: estimate (live window) + billed session usage totals."""
    used_est = estimate_messages(messages)
    window = max(1, int(context_window))
    thr = int(window * float(threshold_percent))
    ratio = used_est / window
    usage = usage_totals or {}
    billed_in = int(usage.get("prompt_tokens") or 0)
    billed_out = int(usage.get("completion_tokens") or 0)
    billed_tot = int(usage.get("total_tokens") or (billed_in + billed_out))

    # headroom until hard threshold
    headroom = max(0, thr - used_est)
    headroom_pct = headroom / max(1, thr)

    bar_w = 20
    filled = min(bar_w, int(round(ratio * bar_w)))
    bar = "█" * filled + "░" * (bar_w - filled)

    level = "ok"
    if thrashing and thrashing.get("thrashing"):
        level = "thrash"
    elif ratio >= float(threshold_percent):
        level = "hot"
    elif ratio >= float(threshold_percent) * 0.75:
        level = "warm"

    return {
        "context_window": window,
        "threshold_percent": threshold_percent,
        "threshold_tokens": thr,
        "used_tokens": used_est,  # live estimate (compat with StatusBar)
        "usage_ratio": round(ratio, 4),
        "estimate_tokens": used_est,
        "estimate_ratio": round(ratio, 4),
        "billed_prompt_tokens": billed_in,
        "billed_completion_tokens": billed_out,
        "billed_total_tokens": billed_tot,
        "headroom_tokens": headroom,
        "headroom_ratio": round(headroom_pct, 4),
        "compress_count": compress_count,
        "messages": len(messages),
        "compact_mode": mode,
        "archive_path": archive_path,
        "thrashing": thrashing or {},
        "level": level,
        "bar": bar,
    }


def format_context_meter(m: dict[str, Any]) -> str:
    thr = m.get("thrashing") or {}
    thr_s = " THRASHING" if thr.get("thrashing") else ""
    arch = m.get("archive_path") or ""
    arch_s = f"\narchive: {arch}" if arch else ""
    return (
        f"ctx [{m.get('bar')}] {m.get('estimate_tokens')}/{m.get('context_window')} "
        f"({float(m.get('estimate_ratio') or 0):.0%}) "
        f"thr@{float(m.get('threshold_percent') or 0):.0%} "
        f"headroom={m.get('headroom_tokens')} "
        f"level={m.get('level')}{thr_s}\n"
        f"billed Σ in={m.get('billed_prompt_tokens')} out={m.get('billed_completion_tokens')} "
        f"total={m.get('billed_total_tokens')}  "
        f"cmp={m.get('compress_count')} msgs={m.get('messages')} mode={m.get('compact_mode')}"
        f"{arch_s}"
    )


async def rag_assist_summary(
    bridge: Any,
    *,
    query: str,
    top_k: int = 5,
) -> str:
    """Advanced: pull Desktop RAG hits into compact continuity note (optional)."""
    if not bridge or not getattr(bridge, "enabled", False):
        return ""
    try:
        from takton_code.bridge.protocol import RAGQuery

        hits = await bridge.rag_search(RAGQuery(query=query[:500], top_k=top_k))
    except Exception as e:  # noqa: BLE001
        return f"(rag unavailable: {e})"
    if not hits:
        return "(rag: no hits)"
    lines = ["[DESKTOP_RAG_CONTEXT]"]
    for h in hits[:top_k]:
        src = getattr(h, "source", None) or (h.get("source") if isinstance(h, dict) else "")
        content = getattr(h, "content", None) or (h.get("content") if isinstance(h, dict) else "")
        score = getattr(h, "score", None) if not isinstance(h, dict) else h.get("score")
        snip = str(content or "").replace("\n", " ")[:240]
        lines.append(f"- ({score}) {src}: {snip}")
    lines.append("[/DESKTOP_RAG_CONTEXT]")
    return "\n".join(lines)
