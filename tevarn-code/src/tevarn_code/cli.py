"""Tevarn Code CLI — OpenCode-class entrypoint."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Optional

import httpx
import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from tevarn_code import __version__
from tevarn_code.agent.loop import AgentRuntime
from tevarn_code.bridge import BridgeConfig, build_bridge
from tevarn_code.config import apply_settings_json, load_settings, save_user_settings_patch
from tevarn_code.llm.provider import build_llm_provider
from tevarn_code.project.binder import bind_project, init_project_files
from tevarn_code.session.store import SessionStore
from tevarn_code.settings.models_cli import register_models_commands
from tevarn_code.settings.models_guide import needs_setup

app = typer.Typer(
    name="tevarn-code",
    add_completion=False,
    no_args_is_help=False,
    help="Tevarn Code — repo-native coding agent",
)
console = Console()
register_models_commands(app)


def _print_event(ev: dict[str, Any], *, json_lines: bool = False) -> None:
    if json_lines:
        # streaming-json: one event per line
        try:
            print(json.dumps(ev, ensure_ascii=False, default=str), flush=True)
        except Exception:
            pass
        return
    t = ev.get("type")
    if t == "text_delta":
        sys.stdout.write(ev.get("text") or "")
        sys.stdout.flush()
        return
    if t == "part":
        p = ev.get("part") or {}
        if p.get("type") == "tool":
            st = (p.get("state") or {}).get("status")
            name = p.get("tool")
            if st == "running":
                console.print(f"  [cyan]→ {name}[/]")
            else:
                console.print(f"  [green]← {name}[/]")
        return
    if t == "compress":
        console.print(
            f"  [yellow]compress[/] #{ev.get('count')} "
            f"{ev.get('before_tokens')}→{ev.get('after_tokens')}"
        )
    elif t == "plan_ready":
        console.print("[magenta]plan ready[/] — /approve")
    elif t == "cancel_requested":
        console.print("[red]cancel requested[/]")
    elif t == "subagent_start":
        console.print(f"  [magenta]subagent[/] {ev.get('agent')}")
    elif t == "error":
        console.print(f"[red]error[/] {ev.get('message')}")


async def _probe_bridge(base_url: str, token: str = "") -> bool:
    url = base_url.rstrip("/") + "/bridge/v1/health"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with httpx.AsyncClient(timeout=2.5) as c:
            r = await c.get(url, headers=headers)
            if r.status_code < 400:
                data = r.json() if r.content else {}
                return bool(data.get("ok", True))
    except Exception:
        return False
    return False


async def _open_runtime(
    path: str | None,
    *,
    mode: str = "build",
    session_id: str | None = None,
    force_bridge: bool | None = None,
    force_local: bool = False,
    worktree: str | bool | None = None,
    worktree_ref: str | None = None,
    event_json: bool = False,
    headless: bool = False,
    permission_profile: str | None = None,
    store: SessionStore | None = None,
    bridge: Any | None = None,
    close_bridge: bool | None = None,
) -> tuple[AgentRuntime, SessionStore, Any]:
    settings = apply_settings_json(load_settings())
    if permission_profile:
        settings.agent.permission_profile = permission_profile  # type: ignore[assignment]
    project = bind_project(
        path,
        worktree=worktree,
        worktree_ref=worktree_ref,
        session_id=session_id,
    )
    if project.is_worktree:
        if not event_json:
            console.print(
                f"[dim]worktree[/] {project.worktree_name}  "
                f"branch={project.branch}  path={project.root}"
            )
    own_store = store is None
    if store is None:
        store = SessionStore(settings.home / "state.db")
        await store.open()

    own_bridge = bridge is None
    if bridge is None:
        bridge_on = settings.bridge.enabled
        if force_local:
            bridge_on = False
        elif force_bridge is True:
            bridge_on = True
        elif force_bridge is None and not settings.bridge.enabled:
            for cand in (
                settings.bridge.base_url,
                "http://127.0.0.1:8090/api",
                "http://127.0.0.1:8000/api",
            ):
                if await _probe_bridge(cand, settings.bridge.api_token):
                    bridge_on = True
                    settings.bridge.base_url = cand
                    if not event_json:
                        console.print(f"[dim]bridge auto-detected: {cand}[/]")
                    break

        bridge_cfg = BridgeConfig(
            enabled=bridge_on,
            base_url=settings.bridge.base_url,
            api_token=settings.bridge.api_token,
            timeout_sec=settings.bridge.timeout_sec,
        )
        bridge = build_bridge(bridge_cfg)
    else:
        bridge_on = bool(getattr(bridge, "enabled", False))

    use_bridge_llm = bridge_on and settings.bridge.use_desktop_models
    llm = build_llm_provider(
        base_url=settings.llm.base_url,
        api_key=settings.llm.api_key,
        model=settings.llm.model,
        temperature=settings.llm.temperature,
        max_tokens=settings.llm.max_tokens,
        bridge=bridge,
        use_bridge=use_bridge_llm,
    )

    def on_ev(ev: dict[str, Any]) -> None:
        _print_event(ev, json_lines=event_json)

    rt = AgentRuntime(
        settings_llm=settings.llm,
        settings_agent=settings.agent,
        project=project,
        store=store,
        llm=llm,
        bridge=bridge,
        mode=mode,
        on_event=on_ev,
        stream=bool(getattr(settings.agent, "stream", True)),
        headless=headless,
    )
    # expose bridge.use_desktop_agent_loop onto agent settings for setup()
    try:
        object.__setattr__(
            settings.agent,
            "use_desktop_agent_loop",
            bool(getattr(settings.bridge, "use_desktop_agent_loop", True)),
        )
    except Exception:
        settings.agent.use_desktop_agent_loop = bool(  # type: ignore[attr-defined]
            getattr(settings.bridge, "use_desktop_agent_loop", True)
        )
    await rt.setup(session_id=session_id)
    if rt.session_id and project.is_worktree:
        await store.bind_worktree(
            rt.session_id,
            worktree_name=project.worktree_name,
            worktree_path=str(project.worktree_path or project.root),
        )
        await store.update_session(
            rt.session_id,
            meta_json=json.dumps(
                {
                    "worktree": {
                        "name": project.worktree_name,
                        "path": project.worktree_path,
                        "main_repo": str(project.main_repo) if project.main_repo else None,
                        "branch": project.branch,
                    }
                },
                ensure_ascii=False,
            ),
            project_root=str(project.root),
        )
    if needs_setup(settings):
        if not event_json and not headless:
            console.print(
                Panel(
                    "[yellow]模型还没配好[/] — 像 openclaw 一样一条命令搞定：\n"
                    "  [bold]tevarn-code models[/]\n"
                    "  [bold]tevarn-code models set local[/]\n"
                    "  [bold]tevarn-code setup[/]",
                    border_style="yellow",
                    title="model setup",
                )
            )
    else:
        if not event_json and not headless:
            console.print(
                f"[dim]model[/] {settings.llm.model}  [dim]@[/] {settings.llm.base_url}"
                + ("  [dim](bridge)[/]" if bridge_on and use_bridge_llm else "")
            )
    # stash ownership hints on runtime for cleanup helpers
    rt._own_store = own_store  # type: ignore[attr-defined]
    rt._own_bridge = own_bridge if close_bridge is None else close_bridge  # type: ignore[attr-defined]
    return rt, store, bridge


async def _resolve_continue_session() -> str | None:
    settings = load_settings()
    store = SessionStore(settings.home / "state.db")
    await store.open()
    try:
        rows = await store.list_sessions(1)
        return rows[0]["id"] if rows else None
    finally:
        await store.close()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    path: Optional[Path] = typer.Option(None, "--path", "-C", help="Project path"),
    prompt: Optional[str] = typer.Option(None, "--prompt", "-p", help="Headless prompt"),
    mode: str = typer.Option("build", "--mode", "-m", help="plan|build|ask|explore|always"),
    session: Optional[str] = typer.Option(None, "--session", "-s", help="Resume session id"),
    continue_last: bool = typer.Option(False, "--continue", "-c", help="Continue last session"),
    yes_build: bool = typer.Option(False, "--yes-build", help="Auto-approve plan (headless)"),
    output: str = typer.Option("text", "--output", help="text|json|streaming-json"),
    check: bool = typer.Option(False, "--check", help="Append self-verification loop (headless)"),
    best_of_n: int = typer.Option(0, "--best-of-n", help="Parallel N candidates (headless only)"),
    autoloop: bool = typer.Option(
        False, "--autoloop", help="Closed loop: plan→build→test→fix (headless or with -p)"
    ),
    autoloop_max_fix: int = typer.Option(3, "--autoloop-max-fix", help="Max fix rounds after test fail"),
    permission_mode: str = typer.Option(
        "default",
        "--permission-mode",
        help="default|acceptEdits|always|bypass|plan|dontAsk",
    ),
    tui: bool = typer.Option(True, "--tui/--no-tui", help="Fullscreen TUI"),
    minimal: bool = typer.Option(False, "--minimal", help="Scrollback-native UI (Grok-style)"),
    mini: bool = typer.Option(False, "--mini", help="Simple REPL (legacy)"),
    bridge: Optional[bool] = typer.Option(None, "--bridge/--no-bridge", help="Desktop bridge"),
    local: bool = typer.Option(False, "--local", help="Force local LLM only"),
    worktree: Optional[str] = typer.Option(
        None,
        "--worktree",
        "-w",
        help="Start in git worktree (omit value or pass name). Use empty string via -w '' for auto name.",
        is_flag=False,
        flag_value="",
    ),
    worktree_ref: Optional[str] = typer.Option(
        None, "--worktree-ref", "--ref", help="Base ref for new worktree (default HEAD)"
    ),
    version: bool = typer.Option(False, "--version", help="Show version"),
) -> None:
    if version:
        console.print(__version__)
        raise typer.Exit(0)
    if ctx.invoked_subcommand is not None:
        return

    perm_profile = _map_permission_mode(permission_mode, mode)
    if permission_mode == "plan":
        mode = "plan"

    sid = session
    if continue_last and not sid:
        sid = asyncio.run(_resolve_continue_session())

    # worktree flag: None=off, "" or name=on
    wt: str | bool | None = None
    if worktree is not None:
        wt = worktree if worktree != "" else True

    if prompt is not None:
        raise typer.Exit(
            asyncio.run(
                _headless(
                    str(path) if path else None,
                    prompt,
                    mode,
                    yes_build,
                    output,
                    sid,
                    bridge,
                    local,
                    wt,
                    worktree_ref,
                    check=check,
                    best_of_n=best_of_n,
                    permission_profile=perm_profile,
                    autoloop=autoloop,
                    autoloop_max_fix=autoloop_max_fix,
                )
            )
        )

    settings = apply_settings_json(load_settings())
    want_minimal = minimal or (
        getattr(settings.ui, "screen_mode", "fullscreen") == "minimal" and not mini
    )

    if want_minimal and sys.stdout.isatty():
        raise typer.Exit(
            asyncio.run(
                _minimal_ui(
                    str(path) if path else None, mode, sid, bridge, local, wt, worktree_ref, perm_profile
                )
            )
        )

    use_tui = tui and not mini and not want_minimal and sys.stdout.isatty()
    if use_tui:
        raise typer.Exit(
            asyncio.run(
                _tui(
                    str(path) if path else None,
                    mode,
                    sid,
                    bridge,
                    local,
                    wt,
                    worktree_ref,
                    permission_profile=perm_profile,
                )
            )
        )
    raise typer.Exit(
        asyncio.run(
            _repl(
                str(path) if path else None,
                mode,
                sid,
                bridge,
                local,
                wt,
                worktree_ref,
                permission_profile=perm_profile,
            )
        )
    )


def _map_permission_mode(permission_mode: str, mode: str) -> str | None:
    """Map Grok-style --permission-mode onto our profile names (local grok help)."""
    pm = (permission_mode or "default").lower().replace("-", "").replace("_", "")
    if pm in ("always", "bypass", "bypasspermissions"):
        return "bypass"  # free allow; deny rules still possible
    if pm in ("acceptedits",):
        return "acceptEdits"
    if pm in ("dontask",):
        return "dontAsk"
    if pm in ("auto", "automode"):
        return "auto"  # Claude auto: local heuristic classifier
    if pm == "plan":
        return "plan"
    if pm in ("free",):
        return "free"
    if mode == "always":
        return "always"
    return None  # keep settings default (cautious)


async def _headless(
    path: str | None,
    prompt: str,
    mode: str,
    yes_build: bool,
    output: str,
    session: str | None,
    bridge: bool | None,
    local: bool,
    worktree: str | bool | None = None,
    worktree_ref: str | None = None,
    check: bool = False,
    best_of_n: int = 0,
    permission_profile: str | None = None,
    autoloop: bool = False,
    autoloop_max_fix: int = 3,
) -> int:
    from tevarn_code.agent.loop import CHECK_SUFFIX

    event_json = output == "streaming-json"
    if check:
        prompt = prompt.rstrip() + CHECK_SUFFIX

    if best_of_n and best_of_n > 1:
        from tevarn_code.compat.backend_core import run_best_of_n

        # bon: free permissions so shell/tests aren't denied headless
        bon_profile = permission_profile or "free"

        async def _open(**kw: Any):
            return await _open_runtime(
                kw.get("path") if "path" in kw else kw.get("path"),
                mode=kw.get("mode", mode),
                session_id=kw.get("session_id"),
                force_bridge=kw.get("force_bridge", bridge),
                force_local=kw.get("force_local", local),
                worktree=kw.get("worktree"),
                worktree_ref=kw.get("worktree_ref"),
                event_json=True,
                headless=True,
                permission_profile=bon_profile,
            )

        # fix open_runtime signature used by bon
        async def open_runtime(
            p: str | None,
            *,
            mode: str = "build",
            session_id: str | None = None,
            force_bridge: bool | None = None,
            force_local: bool = False,
            worktree: str | bool | None = None,
            worktree_ref: str | None = None,
            event_json: bool = True,
        ):
            return await _open_runtime(
                p,
                mode=mode,
                session_id=session_id,
                force_bridge=force_bridge if force_bridge is not None else bridge,
                force_local=force_local or local,
                worktree=worktree,
                worktree_ref=worktree_ref,
                event_json=True,
                headless=True,
                permission_profile=bon_profile,
            )

        result = await run_best_of_n(
            n=best_of_n,
            prompt=prompt,
            path=path,
            open_runtime=open_runtime,
            mode=mode,
            force_bridge=bridge,
            force_local=local,
            keep_worktrees=True,
        )
        if output in ("json", "streaming-json"):
            print(json.dumps(result, ensure_ascii=False, indent=2 if output == "json" else None))
        else:
            w = result.get("winner") or {}
            console.print(Panel(json.dumps(w, ensure_ascii=False, indent=2), title="best-of-n winner"))
            console.print(f"[dim]{result.get('note')}[/]")
        return 0 if result.get("winner") and not (result["winner"] or {}).get("error") else 1

    rt, store, br = await _open_runtime(
        path,
        mode=mode,
        session_id=session,
        force_bridge=bridge,
        force_local=local,
        worktree=worktree,
        worktree_ref=worktree_ref,
        event_json=event_json,
        headless=True,
        permission_profile=permission_profile,
    )
    try:
        if autoloop:
            # plan→build→test→fix; --yes-build auto-approves plan
            al = await rt.run_autoloop(
                prompt,
                max_fix_rounds=autoloop_max_fix,
                auto_approve_plan=bool(yes_build),
                run_tests=True,
            )
            payload = {
                "ok": al.ok,
                "session_id": rt.session_id,
                "autoloop": al.to_dict(),
                "text": al.final_text,
                "error": al.error,
                "plan_state": al.plan_state,
                "test_ok": al.test_ok,
                "checkpoint_ids": al.checkpoint_ids,
                "llm_snapshot": rt.llm_snapshot,
                "usage_totals": getattr(rt, "usage_totals", {}),
                "bridge": bool(getattr(br, "enabled", False)),
            }
            if output == "json":
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            elif output == "streaming-json":
                print(
                    json.dumps({"type": "result", **payload}, ensure_ascii=False, default=str),
                    flush=True,
                )
            else:
                console.print(Panel(Markdown(al.final_text or al.error or ""), title="Autoloop"))
                console.print(
                    f"[dim]ok={al.ok} test_ok={al.test_ok} rounds={len(al.rounds)} "
                    f"chk={','.join(al.checkpoint_ids) or '-'}[/]"
                )
            return 0 if al.ok else 1

        result = await rt.run_turn(prompt)
        if yes_build and rt.plan_gate.state.value == "plan_ready":
            result = await rt.approve_plan_and_build("auto-approved (headless)")
        payload = {
            "ok": result.ok,
            "session_id": rt.session_id,
            "mode": result.mode,
            "plan_state": result.plan_state,
            "interrupted": result.interrupted,
            "compress_count": result.compress_count,
            "iterations": result.iterations,
            "changes": result.changes_summary,
            "text": result.final_text,
            "error": result.error,
            "llm_snapshot": rt.llm_snapshot,
            "usage_totals": getattr(rt, "usage_totals", {}),
            "parts": len(result.parts),
            "bridge": bool(getattr(br, "enabled", False)),
            "worktree": {
                "active": rt.project.is_worktree,
                "name": rt.project.worktree_name,
                "path": rt.project.worktree_path,
                "branch": rt.project.branch,
                "main_repo": str(rt.project.main_repo) if rt.project.main_repo else None,
            },
        }
        if output == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif output == "streaming-json":
            print(
                json.dumps({"type": "result", **payload}, ensure_ascii=False, default=str),
                flush=True,
            )
        else:
            if result.final_text:
                console.print(Panel(Markdown(result.final_text or result.error or ""), title="Tevarn Code"))
            if result.changes_summary and "no file changes" not in result.changes_summary:
                console.print(Panel(result.changes_summary, title="Changes"))
            console.print(
                f"[dim]session={rt.session_id} compress={result.compress_count} "
                f"iter={result.iterations} plan={result.plan_state} bridge={payload['bridge']} "
                f"wt={rt.project.worktree_name or '-'}[/]"
            )
        if result.error:
            return 1
        if result.interrupted:
            return 2
        return 0
    finally:
        await rt.llm.close()
        await br.close()
        await store.close()


async def _minimal_ui(
    path: str | None,
    mode: str,
    session: str | None,
    bridge: bool | None,
    local: bool,
    worktree: str | bool | None = None,
    worktree_ref: str | None = None,
    permission_profile: str | None = None,
) -> int:
    rt, store, br = await _open_runtime(
        path,
        mode=mode,
        session_id=session,
        force_bridge=bridge,
        force_local=local,
        worktree=worktree,
        worktree_ref=worktree_ref,
        permission_profile=permission_profile,
        headless=False,
    )
    try:
        from tevarn_code.tui.minimal import run_minimal

        return int(await run_minimal(rt) or 0)
    finally:
        await rt.llm.close()
        await br.close()
        await store.close()


async def _tui(
    path: str | None,
    mode: str,
    session: str | None,
    bridge: bool | None,
    local: bool,
    worktree: str | bool | None = None,
    worktree_ref: str | None = None,
    permission_profile: str | None = None,
) -> int:
    from tevarn_code.session.hub import SessionHub

    rt, store, br = await _open_runtime(
        path,
        mode=mode,
        session_id=session,
        force_bridge=bridge,
        force_local=local,
        worktree=worktree,
        worktree_ref=worktree_ref,
        permission_profile=permission_profile,
    )
    hub = SessionHub(store)
    await hub.register(rt, make_active=True)

    async def open_session_cb(choice: str | None):
        if choice is None or choice == "__new__":
            nrt, _, _ = await _open_runtime(
                path,
                mode=mode,
                session_id=None,
                force_bridge=bridge,
                force_local=local,
                worktree=worktree,
                worktree_ref=worktree_ref,
                permission_profile=permission_profile,
                store=store,
                bridge=br,
            )
            await hub.register(nrt, make_active=True)
            return nrt
        if choice in {x["id"] for x in hub.list_open()}:
            return await hub.switch(choice)
        nrt, _, _ = await _open_runtime(
            path,
            mode=mode,
            session_id=choice,
            force_bridge=bridge,
            force_local=local,
            worktree=worktree,
            worktree_ref=worktree_ref,
            permission_profile=permission_profile,
            store=store,
            bridge=br,
        )
        await hub.register(nrt, make_active=True)
        return nrt

    try:
        from tevarn_code.tui.app import run_tui

        code = await run_tui(rt, hub=hub, open_session_cb=open_session_cb)
        return int(code or 0)
    finally:
        await hub.close_all()
        await br.close()
        await store.close()


async def _repl(
    path: str | None,
    mode: str,
    session: str | None,
    bridge: bool | None,
    local: bool,
    worktree: str | bool | None = None,
    worktree_ref: str | None = None,
    permission_profile: str | None = None,
) -> int:
    rt, store, br = await _open_runtime(
        path,
        mode=mode,
        session_id=session,
        force_bridge=bridge,
        force_local=local,
        worktree=worktree,
        worktree_ref=worktree_ref,
        permission_profile=permission_profile,
    )
    sess = await store.get_session(rt.session_id or "")
    wt_line = ""
    if rt.project.is_worktree:
        wt_line = f"worktree: {rt.project.worktree_name} ({rt.project.branch})\n"
    console.print(
        Panel.fit(
            f"[bold]Tevarn Code[/] v{__version__}\n"
            f"project: {rt.project.root}\n"
            f"{wt_line}"
            f"session: {(sess or {}).get('slug')} ({rt.session_id})\n"
            f"model: {rt.llm_snapshot.get('model')}\n"
            f"bridge: {bool(getattr(br, 'enabled', False))}\n"
            f"mode: {rt.mode} · /help",
            border_style="magenta",
        )
    )
    try:
        while True:
            try:
                line = console.input("[bold magenta]›[/] ")
            except (EOFError, KeyboardInterrupt):
                console.print("\nbye")
                break
            if not line.strip():
                continue
            if line.strip() in ("/exit", "/quit", "exit", "quit"):
                break
            if line.strip() == "/stop":
                rt.request_cancel()
                continue
            if line.strip() in ("y", "n", "a") and rt.permission_broker and rt.permission_broker.pending:
                dec = {"y": "allow", "n": "deny", "a": "always"}[line.strip()]
                rt.answer_permission_latest(dec)
                continue

            task = asyncio.create_task(rt.run_turn(line))
            try:
                result = await task
            except KeyboardInterrupt:
                rt.request_cancel()
                try:
                    result = await asyncio.wait_for(task, timeout=20)
                except Exception:
                    task.cancel()
                    console.print("cancelled")
                    continue

            if result.error:
                console.print(f"[red]{result.error}[/]")
            if result.final_text:
                console.print(Markdown(result.final_text))
            if result.changes_summary and "no file changes" not in result.changes_summary:
                console.print(Panel(result.changes_summary, title="Changes", border_style="green"))
            if result.interrupted:
                console.print("[yellow]interrupted — /continue[/]")
            console.print(
                f"[dim]mode={result.mode} plan={result.plan_state} "
                f"compress={result.compress_count} iter={result.iterations}[/]"
            )
            while True:
                nxt = await rt.drain_queue_once()
                if not nxt:
                    break
                if nxt.final_text:
                    console.print(Panel(Markdown(nxt.final_text), title="queue"))
        return 0
    finally:
        await rt.llm.close()
        await br.close()
        await store.close()


@app.command("inspect")
def inspect_cmd(path: Optional[Path] = typer.Option(None, "--path", "-C")) -> None:
    """Show project binding, config, bridge (Grok-style inspect)."""

    async def _run() -> None:
        settings = apply_settings_json(load_settings())
        project = bind_project(str(path) if path else None)
        table = Table(title="Tevarn Code Inspect")
        table.add_column("Key")
        table.add_column("Value")
        for k, v in project.to_inspect().items():
            table.add_row(k, json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else str(v))
        table.add_row("llm.base_url", settings.llm.base_url)
        table.add_row("llm.model", settings.llm.model)
        table.add_row("llm.context_window", str(settings.llm.context_window))
        table.add_row("compress_threshold", str(settings.llm.compress_threshold))
        table.add_row("bridge.enabled", str(settings.bridge.enabled))
        table.add_row("bridge.base_url", settings.bridge.base_url)
        table.add_row("home", str(settings.home))
        # live probe
        ok = await _probe_bridge(settings.bridge.base_url, settings.bridge.api_token)
        if not ok:
            ok = await _probe_bridge("http://127.0.0.1:8090/api", settings.bridge.api_token)
            if ok:
                table.add_row("bridge.live", "http://127.0.0.1:8090/api OK")
            else:
                table.add_row("bridge.live", "not detected")
        else:
            table.add_row("bridge.live", f"{settings.bridge.base_url} OK")
        # worktrees
        try:
            from tevarn_code.compat.backend_core import inspect_worktree_state

            wt = inspect_worktree_state(project.root)
            table.add_row("worktrees.count", str(wt.get("count", 0)))
            table.add_row("worktrees.base", str(wt.get("worktrees_base") or ""))
        except Exception as e:
            table.add_row("worktrees", f"n/a ({e})")
        console.print(table)
        from tevarn_code.bridge.protocol import BRIDGE_ROUTES

        console.print(Panel(json.dumps(BRIDGE_ROUTES, indent=2), title="Desktop bridge routes"))

    asyncio.run(_run())


@app.command("init")
def init_cmd(path: Optional[Path] = typer.Option(None, "--path", "-C")) -> None:
    """Write .tevarn/CODE.md template."""
    project = bind_project(str(path) if path else None)
    p = init_project_files(project.root, project.test_command, project.lint_command)
    console.print(f"wrote {p}")


@app.command("sessions")
def sessions_cmd() -> None:
    """List recent sessions."""

    async def _run() -> None:
        settings = load_settings()
        store = SessionStore(settings.home / "state.db")
        await store.open()
        try:
            rows = await store.list_sessions()
            if not rows:
                console.print("(no sessions)")
                return
            t = Table(title="Sessions")
            t.add_column("slug")
            t.add_column("id")
            t.add_column("title")
            t.add_column("mode")
            t.add_column("status")
            t.add_column("cmp")
            for r in rows:
                t.add_row(
                    r.get("slug") or "",
                    (r["id"] or "")[:10],
                    r.get("title") or "",
                    r.get("mode") or "",
                    r.get("status") or "",
                    str(r.get("compress_count") or 0),
                )
            console.print(t)
        finally:
            await store.close()

    asyncio.run(_run())


@app.command("config")
def config_cmd(
    set_kv: Optional[str] = typer.Option(None, "--set", help="key=value nested via dots"),
    list_all: bool = typer.Option(False, "--list"),
) -> None:
    """底层配置。模型请优先用: tevarn-code models / setup"""
    settings = apply_settings_json(load_settings())
    if not set_kv and not list_all:
        console.print(
            Panel(
                "[bold]模型配置请用更浅的入口[/]\n\n"
                "  [cyan]tevarn-code models[/]         看当前 + 预设\n"
                "  [cyan]tevarn-code models set local[/] 一键切本机服务\n"
                "  [cyan]tevarn-code models list[/]    拉远端模型列表\n"
                "  [cyan]tevarn-code models test[/]    连通性\n"
                "  [cyan]tevarn-code setup[/]          向导\n\n"
                "本命令保留给高级 key=value 补丁（--list / --set）。",
                title="config",
                border_style="cyan",
            )
        )
        from tevarn_code.settings.models_guide import format_status_table_rows

        t = Table(show_header=False, box=None)
        t.add_column("k", style="cyan")
        t.add_column("v")
        for k, v in format_status_table_rows(settings):
            t.add_row(k, v)
        console.print(t)
        return
    if list_all:
        console.print_json(settings.model_dump_json(indent=2))
        return
    if set_kv is None or "=" not in set_kv:
        console.print("usage: --set key=value   or use: tevarn-code models set ...")
        raise typer.Exit(1)
    key, val = set_kv.split("=", 1)
    parsed: Any = val
    if val.lower() in ("true", "false"):
        parsed = val.lower() == "true"
    else:
        try:
            parsed = float(val) if "." in val else int(val)
        except ValueError:
            parsed = val
    parts = key.split(".")
    patch: dict[str, Any] = {}
    cur = patch
    for p in parts[:-1]:
        cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = parsed
    out = save_user_settings_patch(patch)
    # keep toml in sync when llm/bridge touched
    try:
        from tevarn_code.settings.models_guide import _mirror_toml

        _mirror_toml(apply_settings_json(out))
    except Exception:
        pass
    console.print(f"updated {out.settings_path()}")
    console.print_json(json.dumps(patch))


@app.command("bridge-check")
def bridge_check() -> None:
    """Ping desktop bridge health."""

    async def _run() -> None:
        settings = apply_settings_json(load_settings())
        for url in (settings.bridge.base_url, "http://127.0.0.1:8090/api", "http://127.0.0.1:8000/api"):
            ok = await _probe_bridge(url, settings.bridge.api_token)
            console.print(f"{url}: {'OK' if ok else 'fail'}")
            if ok:
                br = build_bridge(
                    BridgeConfig(enabled=True, base_url=url, api_token=settings.bridge.api_token)
                )
                try:
                    console.print_json(json.dumps(await br.health(), ensure_ascii=False))
                    models = await br.list_models()
                    console.print(f"models: {len(models)}")
                    skills = await br.list_skills()
                    console.print(f"skills: {len(skills)}")
                    tools = await br.list_tools()
                    console.print(f"tools: {len(tools)}")
                finally:
                    await br.close()
                return

    asyncio.run(_run())


# ---- worktree subcommands (Grok parity) ----
worktree_app = typer.Typer(help="Manage git worktrees for isolated coding sessions")
app.add_typer(worktree_app, name="worktree")


@worktree_app.command("list")
def wt_list(
    path: Optional[Path] = typer.Option(None, "--path", "-C"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List git worktrees for the repo."""
    from tevarn_code.compat.backend_core import WorktreeError, inspect_worktree_state, list_worktrees

    try:
        items = list_worktrees(path or Path.cwd())
    except WorktreeError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    if json_out:
        print(json.dumps([i.to_dict() for i in items], ensure_ascii=False, indent=2))
        return
    t = Table(title="Worktrees")
    t.add_column("name")
    t.add_column("branch")
    t.add_column("HEAD")
    t.add_column("path")
    t.add_column("flags")
    for i in items:
        flags = []
        if i.locked:
            flags.append("locked")
        if i.prunable:
            flags.append("prunable")
        t.add_row(i.name, i.branch or "(detached)", i.head or "", i.path, ",".join(flags))
    console.print(t)
    st = inspect_worktree_state(path or Path.cwd())
    console.print(f"[dim]base: {st.get('worktrees_base')}  main: {st.get('main_repo')}[/]")


@worktree_app.command("add")
def wt_add(
    name: Optional[str] = typer.Argument(None, help="Worktree name (default auto)"),
    path: Optional[Path] = typer.Option(None, "--path", "-C"),
    ref: Optional[str] = typer.Option(None, "--ref", help="Base branch/tag/commit"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Create a worktree under .tevarn/worktrees/<name>."""
    from tevarn_code.compat.backend_core import WorktreeError, add_worktree

    try:
        info = add_worktree(path or Path.cwd(), name=name, ref=ref, force=force)
    except WorktreeError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    console.print(f"[green]created[/] {info.name}  branch={info.branch}  path={info.path}")


@worktree_app.command("show")
def wt_show(
    name: str = typer.Argument(..., help="Worktree name or path"),
    path: Optional[Path] = typer.Option(None, "--path", "-C"),
) -> None:
    from tevarn_code.compat.backend_core import WorktreeError, show_worktree

    try:
        info = show_worktree(path or Path.cwd(), name)
    except WorktreeError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    console.print_json(json.dumps(info.to_dict(), ensure_ascii=False, indent=2))


@worktree_app.command("rm")
def wt_rm(
    name: str = typer.Argument(..., help="Worktree name or path"),
    path: Optional[Path] = typer.Option(None, "--path", "-C"),
    force: bool = typer.Option(False, "--force"),
    delete_branch: bool = typer.Option(False, "--delete-branch", help="Also delete tkc/* branch"),
) -> None:
    from tevarn_code.compat.backend_core import WorktreeError, remove_worktree

    try:
        msg = remove_worktree(path or Path.cwd(), name, force=force, delete_branch=delete_branch)
    except WorktreeError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    console.print(f"[green]{msg}[/]")


@worktree_app.command("gc")
def wt_gc(path: Optional[Path] = typer.Option(None, "--path", "-C")) -> None:
    """Prune stale worktree metadata and empty dirs."""
    from tevarn_code.compat.backend_core import WorktreeError, gc_worktrees

    try:
        msgs = gc_worktrees(path or Path.cwd())
    except WorktreeError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    for m in msgs:
        console.print(m)


@app.command("leader")
def leader_cmd(
    path: Optional[Path] = typer.Option(None, "--path", "-C"),
    mode: str = typer.Option("build", "--mode", "-m"),
) -> None:
    """Run localhost TCP leader (127.0.0.1 only, no token). Writes ~/.tevarn-code/leader.json."""

    async def _run() -> int:
        from tevarn_code.leader.server import LeaderServer
        from tevarn_code.session.hub import SessionHub

        settings = apply_settings_json(load_settings())
        rt, store, br = await _open_runtime(str(path) if path else None, mode=mode)
        hub = SessionHub(store)
        await hub.register(rt)

        async def list_sessions():
            return await hub.list_db_sessions(50)

        async def submit(sid: str | None, text: str):
            target = rt
            if sid and sid != rt.session_id:
                if sid in {x["id"] for x in hub.list_open()}:
                    target = await hub.switch(sid)
                else:
                    nrt, _, _ = await _open_runtime(
                        str(path) if path else None,
                        mode=mode,
                        session_id=sid,
                        store=store,
                        bridge=br,
                    )
                    await hub.register(nrt)
                    target = nrt
            result = await target.run_turn(text)
            return {
                "ok": result.ok,
                "session_id": target.session_id,
                "text": result.final_text,
                "error": result.error,
            }

        async def perm_reply(request_id: str, decision: str) -> bool:
            active = hub.active or rt
            return active.answer_permission(request_id, decision)

        server = LeaderServer(
            home=settings.home,
            list_sessions=list_sessions,
            submit=submit,
            permission_reply=perm_reply,
        )
        host, port = await server.start()
        console.print(f"[green]leader[/] {host}:{port}  file={settings.home / 'leader.json'}")
        console.print("[dim]attach with: tevarn-code attach[/]")
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass
        finally:
            await server.stop()
            await hub.close_all()
            await br.close()
            await store.close()
        return 0

    try:
        raise typer.Exit(asyncio.run(_run()))
    except KeyboardInterrupt:
        raise typer.Exit(0)


@app.command("attach")
def attach_cmd() -> None:
    """Attach to localhost leader (read ~/.tevarn-code/leader.json)."""

    async def _run() -> int:
        from tevarn_code.leader.client import LeaderClient

        settings = apply_settings_json(load_settings())
        try:
            client = LeaderClient.from_home(settings.home)
        except FileNotFoundError as e:
            console.print(f"[red]{e}[/]")
            return 1
        hello = await client.connect()
        console.print_json(json.dumps(hello, ensure_ascii=False, indent=2, default=str))
        console.print("[dim]commands: list | submit <text> | quit[/]")
        try:
            while True:
                try:
                    line = console.input("[cyan]attach›[/] ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not line or line in ("quit", "exit", "/quit"):
                    break
                if line == "list":
                    resp = await client.request({"op": "list_sessions"})
                    console.print_json(json.dumps(resp, ensure_ascii=False, indent=2, default=str))
                    continue
                if line.startswith("submit "):
                    text = line[len("submit ") :]
                    resp = await client.request({"op": "submit", "session_id": None, "text": text})
                    console.print_json(json.dumps(resp, ensure_ascii=False, indent=2, default=str))
                    continue
                console.print("usage: list | submit <text> | quit")
        finally:
            await client.close()
        return 0

    raise typer.Exit(asyncio.run(_run()))


@app.command("auto-rules")
def auto_rules_cmd(
    init: bool = typer.Option(False, "--init", help="Write default ~/.tevarn-code/auto_rules.toml"),
    reload: bool = typer.Option(False, "--reload", help="Force reload cache"),
    path: Optional[Path] = typer.Option(None, "--path", "-C", help="Project path for overlay"),
) -> None:
    """Show auto-mode classifier rules (local TOML, no cloud)."""
    from tevarn_code.agent.auto_classify import (
        clear_rules_cache,
        ensure_default_rules_file,
        format_rules_summary,
        load_rules,
    )

    if init:
        p = ensure_default_rules_file()
        console.print(f"[green]ensured[/] {p}")
    if reload:
        clear_rules_cache()
    root = path.resolve() if path else Path.cwd()
    rs = load_rules(project_root=root, force_reload=reload or init)
    console.print(format_rules_summary(rs))
    from tevarn_code.config import home_dir

    console.print(f"[dim]user: {home_dir() / 'auto_rules.toml'}[/]")
    console.print(f"[dim]project: {root / '.tevarn' / 'auto_rules.toml'}[/]")
    console.print("[dim]env: TEVARN_CODE_AUTO_RULES[/]")


@app.command("export")
def export_cmd(
    session: Optional[str] = typer.Option(None, "--session", "-s", help="Session id (default latest)"),
    fmt: str = typer.Option("json", "--format", "-f", help="json|md|jsonl"),
    path: Optional[Path] = typer.Option(None, "--path", "-C", help="Project root for export dir"),
) -> None:
    """Export a session to .tevarn/exports/."""

    async def _run() -> None:
        settings = load_settings()
        store = SessionStore(settings.home / "state.db")
        await store.open()
        try:
            sid = session
            if not sid:
                rows = await store.list_sessions(1)
                if not rows:
                    console.print("[red]no sessions[/]")
                    raise typer.Exit(1)
                sid = rows[0]["id"]
            data = await store.export_session(sid)
            root = path.resolve() if path else Path((data.get("session") or {}).get("project_root") or Path.cwd())
            from tevarn_code.session.export_fmt import write_export

            out = write_export(root, sid, data, fmt=fmt)
            console.print(f"[green]exported[/] {out}")
        finally:
            await store.close()

    asyncio.run(_run())


@app.command("import-session")
def import_session_cmd(
    file: Path = typer.Argument(..., help="Export .json or .jsonl"),
    path: Optional[Path] = typer.Option(None, "--path", "-C", help="Override project_root"),
) -> None:
    """Import a session export into a new session id."""

    async def _run() -> None:
        from tevarn_code.session.export_fmt import load_export_file

        settings = load_settings()
        store = SessionStore(settings.home / "state.db")
        await store.open()
        try:
            data = load_export_file(file)
            nid = await store.import_session_data(
                data, project_root=str(path.resolve()) if path else None
            )
            console.print(f"[green]imported[/] session {nid}")
        finally:
            await store.close()

    asyncio.run(_run())


@app.command("stats")
def stats_cmd(
    days: Optional[int] = typer.Option(None, "--days", "-d", help="Only last N days"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Token/session stats from local state.db."""

    async def _run() -> None:
        settings = load_settings()
        store = SessionStore(settings.home / "state.db")
        await store.open()
        try:
            st = await store.stats_summary(days=days)
            if json_out:
                console.print_json(data=st)
                return
            console.print(
                f"sessions={st['sessions']}  tokens_in={st['tokens_input']}  "
                f"tokens_out={st['tokens_output']}  compress_sum={st['compress_count_sum']}"
            )
            for r in st.get("recent") or []:
                console.print(
                    f"  {(r.get('slug') or '')[:16]}  {(r.get('id') or '')[:10]}  "
                    f"in={r.get('tokens_input') or 0} out={r.get('tokens_output') or 0}"
                )
        finally:
            await store.close()

    asyncio.run(_run())


@app.command("pr")
def pr_cmd(
    pr: str = typer.Argument(..., help="PR number or URL"),
    path: Optional[Path] = typer.Option(None, "--path", "-C"),
) -> None:
    """Checkout a GitHub PR via gh CLI."""
    from tevarn_code.project.pr_checkout import checkout_pr, gh_available

    if not gh_available():
        console.print("[red]gh CLI not found[/]")
        raise typer.Exit(1)
    root = path.resolve() if path else Path.cwd()
    res = checkout_pr(root, pr)
    if res.get("ok"):
        console.print(f"[green]ok[/]\n{res.get('output')}")
    else:
        console.print(f"[red]failed[/] {res.get('error') or ''}\n{res.get('output') or ''}")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
