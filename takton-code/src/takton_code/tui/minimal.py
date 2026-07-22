"""Minimal scrollback UI — history in native terminal; sticky current turn."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from takton_code import __version__
from takton_code.agent.loop import AgentRuntime
from takton_code.tui.renderer import format_event_lines
from takton_code.tui.stream_buffer import StreamBuffer


console = Console(stderr=False)


async def run_minimal(runtime: AgentRuntime) -> int:
    """Grok-style minimal: finalized lines go to scrollback; stream sticks to one line."""
    runtime.stream = bool(getattr(runtime.settings_agent, "stream", True))
    flush_chars = 1
    flush_ms = 16
    try:
        from takton_code.config import apply_settings_json, load_settings

        ui = apply_settings_json(load_settings()).ui
        flush_chars = int(ui.stream_flush_chars)
        flush_ms = int(ui.stream_flush_ms)
    except Exception:
        pass

    stream = StreamBuffer(flush_chars=flush_chars, flush_ms=flush_ms)
    stream_acc = ""
    pending_perm: dict[str, Any] | None = None
    perm_event = asyncio.Event()

    def paint_stream(chunk: str) -> None:
        nonlocal stream_acc
        stream_acc += chunk
        # single sticky line via carriage return
        shown = stream_acc.replace("\n", " ")[-120:]
        sys.stdout.write("\r\033[K" + shown)
        sys.stdout.flush()

    def commit_stream() -> None:
        nonlocal stream_acc
        left = stream.flush()
        if left:
            stream_acc += left
        if stream_acc.strip():
            sys.stdout.write("\r\033[K")
            console.print(stream_acc)
        stream_acc = ""

    def on_event(ev: dict[str, Any]) -> None:
        nonlocal pending_perm
        t = ev.get("type")
        if t == "permission_request":
            pending_perm = ev
            perm_event.set()
            console.print(
                f"[yellow]ASK[/] {ev.get('tool')}: {escape(str(ev.get('summary') or ''))}\n"
                f"  [y]allow [n]deny [a]always-for-tool   id={ev.get('request_id')}"
            )
            return
        if t == "text_delta":
            piece = stream.push(ev.get("text") or "")
            if piece:
                paint_stream(piece)
            return
        if t == "reasoning_delta":
            # light dim line, don't disrupt stream much
            return

        # other events: flush stream first
        commit_stream()
        for line in format_event_lines(ev):
            style = {
                "user": "bold",
                "assistant": "",
                "tool": "cyan",
                "reasoning": "dim italic",
                "system": "magenta",
                "status": "yellow",
                "permission": "yellow",
            }.get(line.kind, "")
            if line.kind == "tool" and not line.sticky:
                console.print(f"[{style}]{escape(line.text)}[/]" if style else escape(line.text))
            elif line.sticky and line.kind == "system":
                console.print(f"[dim]{escape(line.text)}[/]")
            else:
                console.print(f"[{style}]{escape(line.text)}[/]" if style else escape(line.text))

    prev = runtime.on_event

    def combined(ev: dict[str, Any]) -> None:
        if prev:
            prev(ev)
        on_event(ev)

    runtime.on_event = combined

    console.print(
        Panel.fit(
            f"[bold magenta]Takton Code[/] v{__version__}  [dim]minimal scrollback[/]\n"
            f"project: {runtime.project.root}\n"
            f"session: {runtime.session_id}\n"
            f"model: {runtime.llm_snapshot.get('model')}\n"
            f"mode: {runtime.mode}  bridge: {bool(getattr(runtime.bridge,'enabled',False))}\n"
            f"[dim]Tab-less: /build /plan /always · run中 plain=steer · /enqueue · y/n/a=perm[/]",
            border_style="magenta",
        )
    )

    try:
        while True:
            # drain permission if any before prompt
            if pending_perm and not runtime._running:
                pending_perm = None
                perm_event.clear()

            try:
                line = await asyncio.to_thread(lambda: console.input("[bold magenta]›[/] "))
            except (EOFError, KeyboardInterrupt):
                console.print("\nbye")
                break
            text = (line or "").strip()
            if not text:
                continue
            if text in ("/exit", "/quit", "exit", "quit"):
                break
            if text == "/stop":
                runtime.request_cancel()
                continue

            # permission short replies
            if pending_perm and text.lower() in ("y", "n", "a", "yes", "no", "always"):
                dec = {"y": "allow", "yes": "allow", "n": "deny", "no": "deny", "a": "always", "always": "always"}[
                    text.lower()
                ]
                rid = str(pending_perm.get("request_id") or "")
                runtime.answer_permission(rid, dec)
                pending_perm = None
                perm_event.clear()
                continue
            if text.startswith("/allow"):
                runtime.answer_permission_latest("allow")
                continue
            if text.startswith("/deny"):
                runtime.answer_permission_latest("deny")
                continue
            if text in ("/always-tool", "/perm-always"):
                runtime.answer_permission_latest("always")
                continue

            if runtime._running:
                if text.startswith("/enqueue "):
                    body = text[len("/enqueue ") :].strip()
                    if body:
                        await runtime.enqueue(body)
                        console.print(f"[cyan]queued[/] {body}")
                    continue
                if text.startswith("/") and not text.startswith("/steer"):
                    console.print("[dim]busy — plain text steers; /enqueue queues[/]")
                    continue
                body = text[7:] if text.startswith("/steer ") else text
                runtime.steer(body)
                console.print(f"[yellow]steer[/] {body}")
                continue

            console.print(f"\n[bold]› {escape(text)}[/]\n")
            # run turn while polling for permission prompts
            task = asyncio.create_task(runtime.run_turn(text))
            while not task.done():
                if pending_perm:
                    try:
                        ans = await asyncio.to_thread(
                            lambda: console.input("[yellow]perm›[/] [y/n/a] ").strip().lower()
                        )
                    except (EOFError, KeyboardInterrupt):
                        runtime.answer_permission_latest("deny")
                        pending_perm = None
                        continue
                    dec = {"y": "allow", "yes": "allow", "n": "deny", "no": "deny", "a": "always", "always": "always"}.get(
                        ans, "deny"
                    )
                    rid = str((pending_perm or {}).get("request_id") or "")
                    if rid:
                        runtime.answer_permission(rid, dec)
                    else:
                        runtime.answer_permission_latest(dec)
                    pending_perm = None
                await asyncio.sleep(0.05)
            try:
                result = await task
            except Exception as e:  # noqa: BLE001
                commit_stream()
                console.print(f"[red]turn failed: {e}[/]")
                continue
            commit_stream()
            if result.error:
                console.print(f"[red]{result.error}[/]")
            if result.changes_summary and "no file changes" not in result.changes_summary:
                console.print(Panel(result.changes_summary, title="Changes", border_style="green"))
            if result.interrupted:
                console.print("[yellow]interrupted — /continue[/]")
            # auto drain queue one
            try:
                nxt = await runtime.drain_queue_once()
                if nxt and nxt.final_text:
                    console.print(Panel(nxt.final_text, title="queue", border_style="cyan"))
            except Exception:
                pass
        return 0
    finally:
        runtime.on_event = prev
