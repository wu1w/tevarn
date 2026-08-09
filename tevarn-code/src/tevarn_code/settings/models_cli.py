"""Interactive + CLI model setup (OpenClaw-simple)."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from tevarn_code.settings.models_guide import (
    PRESETS,
    apply_llm_patch,
    apply_preset,
    current_settings,
    current_snapshot,
    format_status_table_rows,
    needs_setup,
    probe_bridge,
    probe_models,
    test_chat,
)

console = Console()


def _print_current() -> None:
    s = current_settings()
    snap = current_snapshot(s)
    rows = format_status_table_rows(s)
    t = Table(title="当前模型", show_header=False, box=None, padding=(0, 2))
    t.add_column("k", style="cyan")
    t.add_column("v", style="bold")
    for k, v in rows:
        t.add_row(k, v)
    console.print(t)
    if needs_setup(s):
        console.print(
            Panel(
                "[yellow]还没配置过模型[/]\n"
                "一键：  [bold]tevarn-code models set local[/]\n"
                "向导：  [bold]tevarn-code setup[/]\n"
                "自定义：[bold]tevarn-code models use --base-url URL --model NAME[/]",
                title="提示",
                border_style="yellow",
            )
        )
    else:
        mode = "桌面 Bridge" if s.bridge.enabled and s.bridge.use_desktop_models else "直连 LLM"
        console.print(f"[dim]模式: {mode} · 改完立即生效（新会话）[/]")


def _print_presets() -> None:
    t = Table(title="预设（tevarn-code models set <id>）")
    t.add_column("id", style="green bold")
    t.add_column("名称")
    t.add_column("base_url")
    t.add_column("model")
    t.add_column("说明", style="dim")
    for p in PRESETS.values():
        t.add_row(p.id, p.name, p.base_url, p.model, p.note)
    console.print(t)


def register_models_commands(app: typer.Typer) -> None:
    """Attach models / setup to root app."""

    models_app = typer.Typer(
        help="模型配置（一眼看懂，对标 openclaw config）",
        invoke_without_command=True,
        no_args_is_help=False,
    )
    app.add_typer(models_app, name="models")
    # alias
    app.add_typer(models_app, name="model")

    @models_app.callback(invoke_without_command=True)
    def models_root(ctx: typer.Context) -> None:
        if ctx.invoked_subcommand is not None:
            return
        _print_current()
        console.print()
        _print_presets()
        console.print(
            "\n[dim]命令: set · use · list · test · show · doctor · setup[/]"
        )

    @models_app.command("show")
    def models_show() -> None:
        """显示当前模型（默认 models 同款）。"""
        _print_current()

    @models_app.command("presets")
    def models_presets() -> None:
        _print_presets()

    @models_app.command("set")
    def models_set(
        target: str = typer.Argument(..., help="预设 id，或直接写模型名（保留当前 base_url）"),
        api_key: Optional[str] = typer.Option(None, "--api-key", "-k"),
        base_url: Optional[str] = typer.Option(None, "--base-url", "-u"),
        test_after: bool = typer.Option(True, "--test/--no-test", help="设置后自动连通性测试"),
    ) -> None:
        """切换预设或模型。例: models set local · models set gpt-4.1 -u https://..."""
        tid = target.strip()
        try:
            if tid.lower() in PRESETS and base_url is None:
                s = apply_preset(tid, api_key=api_key)
                console.print(f"[green]✓ 已切换预设[/] [bold]{tid}[/]")
            else:
                # treat as model id
                s = apply_llm_patch(
                    model=tid,
                    base_url=base_url,
                    api_key=api_key,
                    bridge_enabled=False if base_url else None,
                    use_desktop_models=False if base_url else None,
                )
                console.print(f"[green]✓ 模型已设为[/] [bold]{tid}[/]")
        except KeyError as e:
            console.print(f"[red]{e}[/]")
            _print_presets()
            raise typer.Exit(1)

        _print_current()
        if test_after:
            asyncio.run(_run_test(s))

    @models_app.command("use")
    def models_use(
        base_url: str = typer.Option(..., "--base-url", "-u", help="OpenAI-compatible base, e.g. http://host:8088/v1"),
        model: str = typer.Option(..., "--model", "-m"),
        api_key: str = typer.Option("no-key", "--api-key", "-k"),
        context_window: int = typer.Option(65536, "--ctx", help="context window tokens"),
        max_tokens: int = typer.Option(4096, "--max-tokens"),
        temperature: float = typer.Option(0.2, "--temperature", "-t"),
        test_after: bool = typer.Option(True, "--test/--no-test"),
    ) -> None:
        """完整自定义一条连接（最常用的「高级」入口，仍然很浅）。"""
        s = apply_llm_patch(
            base_url=base_url,
            model=model,
            api_key=api_key,
            context_window=context_window,
            max_tokens=max_tokens,
            temperature=temperature,
            provider="openai_compatible",
            bridge_enabled=False,
            use_desktop_models=False,
        )
        console.print("[green]✓ 已保存[/]")
        _print_current()
        if test_after:
            asyncio.run(_run_test(s))

    @models_app.command("list")
    def models_list(
        base_url: Optional[str] = typer.Option(None, "--base-url", "-u", help="默认用当前配置"),
        api_key: Optional[str] = typer.Option(None, "--api-key", "-k"),
    ) -> None:
        """拉取远端 /v1/models 列表。"""
        s = current_settings()
        url = base_url or s.llm.base_url
        key = api_key if api_key is not None else s.llm.api_key

        async def _go() -> dict[str, Any]:
            if s.bridge.enabled and s.bridge.use_desktop_models and not base_url:
                return await probe_bridge(s.bridge.base_url, s.bridge.api_token)
            return await probe_models(url, key or "no-key")

        res = asyncio.run(_go())
        if not res.get("ok"):
            console.print(f"[red]失败[/] {res.get('error')}")
            raise typer.Exit(1)
        models = res.get("models") or []
        console.print(f"[green]OK[/] {res.get('url') or res.get('url')} · {len(models)} models")
        for i, m in enumerate(models[:50], 1):
            mark = " ← current" if m == s.llm.model else ""
            console.print(f"  {i:2}. {m}{mark}")
        if len(models) > 50:
            console.print(f"  … +{len(models)-50} more")
        console.print("\n[dim]选用: tevarn-code models set <model名>[/]")

    @models_app.command("test")
    def models_test() -> None:
        """对当前配置发一条 PONG 测试。"""
        s = current_settings()
        asyncio.run(_run_test(s))

    @models_app.command("doctor")
    def models_doctor() -> None:
        """检查配置文件、连通性、bridge。"""
        s = current_settings()
        _print_current()
        console.print()

        async def _go() -> None:
            console.print("[bold]1. 直连 LLM /v1/models[/]")
            r = await probe_models(s.llm.base_url, s.llm.api_key)
            if r.get("ok"):
                console.print(f"  [green]OK[/] {r.get('url')} · {r.get('count')} models")
                if s.llm.model not in (r.get("models") or []) and (r.get("models") or []):
                    console.print(
                        f"  [yellow]警告[/] 当前 model={s.llm.model!r} 不在远端列表，"
                        f"可先: models list && models set <名>"
                    )
            else:
                console.print(f"  [red]FAIL[/] {r.get('error')}")

            console.print("[bold]2. Chat 冒烟[/]")
            if r.get("ok") or True:
                t = await test_chat(
                    base_url=s.llm.base_url,
                    model=s.llm.model,
                    api_key=s.llm.api_key,
                )
                if t.get("ok"):
                    console.print(
                        f"  [green]OK[/] {t.get('latency_ms')}ms · reply={t.get('reply')!r}"
                    )
                else:
                    console.print(f"  [red]FAIL[/] {t.get('error')}")

            console.print("[bold]3. Desktop bridge[/]")
            b = await probe_bridge(s.bridge.base_url, s.bridge.api_token)
            if b.get("ok"):
                console.print(
                    f"  [green]OK[/] {b.get('url')} · desktop models={b.get('models')}"
                )
            else:
                console.print(f"  [yellow]offline[/] {b.get('error')}")

            console.print("[bold]4. 配置文件[/]")
            console.print(f"  settings.json  {s.settings_path()}  exists={s.settings_path().is_file()}")
            console.print(f"  config.toml    {s.config_toml_path()}  exists={s.config_toml_path().is_file()}")

        asyncio.run(_go())

    @app.command("setup")
    def setup_cmd(
        yes: Optional[str] = typer.Option(
            None,
            "--preset",
            "-p",
            help="非交互：直接套用预设 id（local/ollama/desktop/...）",
        ),
    ) -> None:
        """首次模型配置向导（交互）。"""
        if yes:
            try:
                apply_preset(yes)
            except KeyError as e:
                console.print(f"[red]{e}[/]")
                raise typer.Exit(1)
            console.print(f"[green]✓ setup preset={yes}[/]")
            _print_current()
            asyncio.run(_run_test(current_settings()))
            return
        asyncio.run(_wizard())


async def _run_test(s: Any) -> None:
    console.print("[dim]测试连通性…[/]")
    if getattr(s.bridge, "enabled", False) and getattr(s.bridge, "use_desktop_models", False):
        b = await probe_bridge(s.bridge.base_url, s.bridge.api_token)
        if b.get("ok"):
            console.print(f"[green]✓ bridge OK[/] models={b.get('models')}")
        else:
            console.print(f"[red]✗ bridge[/] {b.get('error')}")
        return
    r = await probe_models(s.llm.base_url, s.llm.api_key)
    if not r.get("ok"):
        console.print(f"[red]✗ models[/] {r.get('error')}")
        return
    console.print(f"[green]✓ models[/] {r.get('count')} @ {r.get('url')}")
    t = await test_chat(base_url=s.llm.base_url, model=s.llm.model, api_key=s.llm.api_key)
    if t.get("ok"):
        console.print(f"[green]✓ chat[/] {t.get('latency_ms')}ms → {t.get('reply')!r}")
    else:
        console.print(f"[red]✗ chat[/] {t.get('error')}")


async def _wizard() -> None:
    console.print(
        Panel.fit(
            "[bold]Tevarn Code 模型设置向导[/]\n"
            "像 openclaw config 一样：选预设 → 测通 → 完成\n"
            "随时可再跑 [cyan]tevarn-code models[/] / [cyan]tevarn-code setup[/]",
            border_style="magenta",
        )
    )
    _print_current()
    console.print()
    _print_presets()
    console.print()

    choice = Prompt.ask(
        "选预设 id（或输入 custom）",
        default="local",
        choices=list(PRESETS.keys()) + ["custom"],
    )

    if choice == "custom":
        base = Prompt.ask("base_url", default="http://127.0.0.1:8088/v1")
        console.print("[dim]正在拉取模型列表…[/]")
        key = Prompt.ask("api_key（无则回车）", default="no-key")
        probed = await probe_models(base, key)
        if probed.get("ok") and probed.get("models"):
            console.print("可用模型:")
            for i, m in enumerate(probed["models"][:30], 1):
                console.print(f"  {i}. {m}")
            model = Prompt.ask("model 名", default=str(probed["models"][0]))
        else:
            console.print(f"[yellow]列表失败[/] {probed.get('error')} — 手动填 model")
            model = Prompt.ask("model 名", default="default")
        ctx = int(Prompt.ask("context_window", default="65536"))
        apply_llm_patch(
            base_url=base,
            model=model,
            api_key=key,
            context_window=ctx,
            provider="openai_compatible",
            bridge_enabled=False,
            use_desktop_models=False,
        )
    else:
        p = PRESETS[choice]
        key = p.api_key
        if choice in ("openai", "deepseek", "xfyun"):
            key = Prompt.ask(f"{p.name} api_key", default="")
        apply_preset(choice, api_key=key or None)
        console.print(f"[green]✓ 已应用预设 {choice}[/] — {p.name}")

    s = current_settings()
    _print_current()
    if Confirm.ask("现在测试连通性？", default=True):
        await _run_test(s)
    console.print(
        Panel(
            "[green]完成[/]\n"
            "启动: [bold]tevarn-code[/]\n"
            "查看: [bold]tevarn-code models[/]\n"
            "改模型: [bold]tevarn-code models set <名>[/]",
            border_style="green",
        )
    )
