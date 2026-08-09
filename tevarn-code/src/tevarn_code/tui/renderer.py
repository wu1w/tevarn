"""Pure event → render lines (shared by fullscreen / minimal / headless)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class RenderLine:
    kind: str  # user|assistant|tool|reasoning|system|status|permission
    text: str
    sticky: bool = False  # True = current-turn pane only (minimal)


def format_event_lines(ev: dict[str, Any]) -> list[RenderLine]:
    """Pure formatter — no I/O. Both UIs consume this."""
    t = ev.get("type")
    lines: list[RenderLine] = []

    if t == "text_delta":
        text = ev.get("text") or ""
        if text:
            lines.append(RenderLine("assistant", text, sticky=True))
        return lines

    if t == "reasoning_delta":
        piece = (ev.get("text") or "").replace("\n", " ").strip()
        if piece:
            lines.append(RenderLine("reasoning", f"… {piece[:200]}", sticky=True))
        return lines

    if t == "part":
        p = ev.get("part") or {}
        pt = p.get("type")
        if pt == "step-start":
            n = (p.get("meta") or {}).get("n", "?")
            lines.append(RenderLine("system", f"── step {n} ──", sticky=True))
        elif pt == "step-finish":
            reason = p.get("reason") or ""
            lines.append(RenderLine("system", f"└ step done ({reason})", sticky=True))
        elif pt == "reasoning":
            txt = (p.get("text") or "").replace("\n", " ")[:400]
            if txt:
                lines.append(RenderLine("reasoning", f"thinking ▾ {txt}", sticky=True))
        elif pt == "tool":
            st = p.get("state") or {}
            name = str(p.get("tool") or "?")
            status = st.get("status")
            if status == "running":
                inp = st.get("input")
                prev = ""
                if isinstance(inp, str) and inp:
                    prev = inp.replace("\n", " ")[:80]
                lines.append(RenderLine("tool", f"● {name} {prev}".rstrip(), sticky=True))
            else:
                mark = "✓" if status == "completed" else "✗"
                out = str(st.get("output") or "").replace("\n", " ")[:120]
                # completed tools leave sticky so minimal can pin them into scrollback
                lines.append(RenderLine("tool", f"{mark} {name} {out}".rstrip(), sticky=False))
        elif pt == "text":
            role = (p.get("meta") or {}).get("role")
            body = p.get("text") or ""
            if role == "user":
                lines.append(RenderLine("user", body, sticky=False))
            elif role == "steer":
                lines.append(RenderLine("system", f"steer: {body}", sticky=True))
            elif body:
                lines.append(RenderLine("assistant", body, sticky=False))
        return lines

    if t == "tool_start":
        lines.append(RenderLine("tool", f"● {ev.get('name')}", sticky=True))
    elif t == "tool_end":
        prev = str(ev.get("result_preview") or "").replace("\n", " ")[:100]
        lines.append(RenderLine("tool", f"✓ {prev}", sticky=False))
    elif t == "compress":
        lines.append(
            RenderLine(
                "status",
                f"⟳ compress #{ev.get('count')} {ev.get('before_tokens')}→{ev.get('after_tokens')}",
                sticky=False,
            )
        )
    elif t == "plan_ready":
        lines.append(RenderLine("system", "Plan ready — /approve", sticky=False))
        plan = ev.get("plan") or {}
        for i, step in enumerate(plan.get("steps") or [], 1):
            lines.append(RenderLine("system", f"  {i}. {step.get('title')}", sticky=False))
    elif t == "cancel_requested":
        lines.append(RenderLine("system", "■ stop requested", sticky=True))
    elif t == "queue":
        lines.append(
            RenderLine(
                "status",
                f"queue {ev.get('action')}: {str(ev.get('content') or '')[:60]}",
                sticky=False,
            )
        )
    elif t == "undo":
        lines.append(RenderLine("system", f"undo turn {ev.get('turn_id')}", sticky=False))
    elif t == "rewind":
        pid = ev.get("point_id") or "?"
        n = len(ev.get("restored") or [])
        lines.append(RenderLine("system", f"rewind {pid} ({n} files)", sticky=False))
    elif t == "autoloop":
        phase = ev.get("phase") or ""
        lines.append(RenderLine("system", f"autoloop:{phase}", sticky=False))
    elif t == "history_point":
        p = ev.get("point") or {}
        lines.append(
            RenderLine(
                "system",
                f"checkpoint {p.get('id')} files={p.get('file_count')} {p.get('label')}",
                sticky=False,
            )
        )
    elif t == "subagent_start":
        lines.append(
            RenderLine(
                "system",
                f"↳ subagent {ev.get('agent')}: {str(ev.get('prompt') or '')[:60]}",
                sticky=True,
            )
        )
    elif t == "subagent_end":
        lines.append(RenderLine("system", f"↳ subagent done chars={ev.get('chars')}", sticky=False))
    elif t == "permission_request":
        lines.append(
            RenderLine(
                "permission",
                f"ASK [{ev.get('request_id')}] {ev.get('tool')}: {ev.get('summary')}",
                sticky=True,
            )
        )
    elif t == "permission_resolved":
        lines.append(
            RenderLine(
                "permission",
                f"permission {ev.get('decision')} for {ev.get('tool')}",
                sticky=False,
            )
        )
    elif t == "error":
        lines.append(RenderLine("system", f"error: {ev.get('message')}", sticky=False))
    elif t == "user":
        lines.append(RenderLine("user", str(ev.get("content") or ""), sticky=False))

    return lines


class EventRenderer(Protocol):
    def on_event(self, ev: dict[str, Any]) -> None: ...
