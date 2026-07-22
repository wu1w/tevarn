"""Fullscreen TUI — mouse + keyboard dual input everywhere."""

from __future__ import annotations

import asyncio
from typing import Any

from rich.markup import escape
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
)

from takton_code.agent.loop import AgentRuntime
from takton_code.agent.refs import filter_slash_commands
from takton_code.session.hub import SessionHub
from takton_code.tui.renderer import format_event_lines
from takton_code.tui.stream_buffer import StreamBuffer


# ── shared CSS bits for modal toolbars ──────────────────────────────────────
_MODAL_CSS = """
.modal-title { padding: 1; color: #a371f7; text-style: bold; }
.modal-status { padding: 0 1 1 1; color: #8b949e; }
.modal-btns { height: auto; padding: 1; background: #161b22; }
.modal-btns Button { margin-right: 1; margin-bottom: 0; }
.modal-scroll {
    height: 1fr;
    border: solid #30363d;
    background: #0d1117;
    padding: 0 1;
}
Checkbox { padding: 0 1; background: #0d1117; }
Checkbox:hover { background: #21262d; }
Checkbox.-on { color: #3fb950; }
ListView { height: 1fr; border: solid #30363d; }
ListItem:hover { background: #21262d; }
"""


class StatusBar(Static):
    def update_status(self, data: dict[str, Any]) -> None:
        mode = str(data.get("mode", "?")).upper()
        model = escape(str(data.get("model", "?"))[:36])
        tokens = data.get("tokens") or {}
        used = tokens.get("used_tokens") or tokens.get("estimate_tokens") or 0
        window = tokens.get("context_window", 0)
        ratio = tokens.get("usage_ratio") or tokens.get("estimate_ratio") or 0
        level = str(tokens.get("level") or "")
        thrash = tokens.get("thrashing") or {}
        thrash_on = bool(thrash.get("thrashing")) if isinstance(thrash, dict) else bool(data.get("thrashing"))
        compress = data.get("compress_count", 0)
        bridge = "bridge:on" if data.get("bridge") else "bridge:off"
        plan = data.get("plan_state", "idle")
        slug = escape(str(data.get("slug") or data.get("session_id") or "")[:14])
        qn = data.get("queue_n", 0)
        qbadge = f" queue:{qn}" if qn else ""
        usage = data.get("usage_totals") or {}
        tot = usage.get("total_tokens") or tokens.get("billed_total_tokens") or 0
        tin = usage.get("prompt_tokens") or tokens.get("billed_prompt_tokens") or 0
        tout = usage.get("completion_tokens") or tokens.get("billed_completion_tokens") or 0
        tok_badge = f" Σ{tin}↑/{tout}↓" if (tin or tout or tot) else ""
        ask = " ASK" if data.get("perm_pending") else ""
        lvl = f" {level}" if level else ""
        thr = " [THRASH]" if thrash_on else ""
        bar = tokens.get("bar") or ""
        bar_s = f" {bar}" if bar else ""
        cmode = tokens.get("compact_mode") or ""
        cm = f" mode={cmode}" if cmode else ""
        self.update(
            f" [bold magenta]{mode}[/] │ {model} │ "
            f"ctx{bar_s} {used}/{window} ({float(ratio):.0%}){lvl}{thr} │ cmp={compress}{cm} │ "
            f"plan={plan} │ {bridge} │ {slug}{qbadge}{tok_badge}{ask}"
        )


class DashboardScreen(ModalScreen[str | None]):
    """Session picker — click row or buttons."""

    BINDINGS = [
        Binding("escape", "cancel", "Close"),
        Binding("n", "new_session", "New", show=True),
        Binding("enter", "confirm", "Open", show=True),
    ]

    def __init__(self, rows: list[dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.rows = rows
        self._selected: str | None = None
        for r in rows:
            if r.get("active"):
                self._selected = r.get("id")
                break
        if self._selected is None and rows:
            self._selected = rows[0].get("id")

    def compose(self) -> ComposeResult:
        yield Label("Sessions — 点击行切换 · 底部按钮 · n 新建 · Esc 关闭", classes="modal-title")
        yield Static("", id="dash-status", classes="modal-status")
        items = []
        for i, r in enumerate(self.rows):
            mark = "*" if r.get("active") else ("o" if r.get("open") else " ")
            run = "RUN" if r.get("running") else "   "
            label = (
                f"{mark} {run} {(r.get('slug') or r.get('id') or '')[:16]}  "
                f"{r.get('mode') or ''}  {(r.get('title') or '')[:40]}"
            )
            items.append(ListItem(Label(label), id=f"s_{i}"))
        yield ListView(*items, id="dash-list")
        with Horizontal(classes="modal-btns"):
            yield Button("打开 Open", variant="primary", id="dash-open")
            yield Button("新建 New", id="dash-new")
            yield Button("关闭 Esc", id="dash-close")
        self.call_after_refresh(self._refresh)

    def _refresh(self) -> None:
        try:
            self.query_one("#dash-status", Static).update(
                f"selected={(self._selected or '-')[:20]}  ·  双击/Enter 打开"
            )
        except Exception:
            pass

    def _sync_index(self) -> None:
        try:
            lv = self.query_one("#dash-list", ListView)
            idx = lv.index
            if idx is not None and 0 <= idx < len(self.rows):
                self._selected = self.rows[idx].get("id")
                self._refresh()
        except Exception:
            pass

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        iid = event.item.id or ""
        if iid.startswith("s_"):
            try:
                idx = int(iid[2:])
                self._selected = self.rows[idx].get("id")
                self._refresh()
            except (ValueError, IndexError):
                pass

    def on_list_view_highlighted(self, event: Any) -> None:
        self._sync_index()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "dash-open":
            self.action_confirm()
        elif bid == "dash-new":
            self.action_new_session()
        elif bid == "dash-close":
            self.action_cancel()

    def action_confirm(self) -> None:
        self._sync_index()
        if self._selected:
            self.dismiss(self._selected)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_new_session(self) -> None:
        self.dismiss("__new__")


class RewindScreen(ModalScreen[dict[str, Any] | None]):
    """Checkpoint picker with mouse buttons for scope / preview / files."""

    BINDINGS = [
        Binding("escape", "cancel", "Close"),
        Binding("1", "scope_code", "Code", show=True),
        Binding("2", "scope_conv", "Chat", show=True),
        Binding("3", "scope_both", "Both", show=True),
        Binding("p", "preview", "Preview", show=True),
        Binding("f", "files_mode", "Files", show=True),
        Binding("enter", "confirm", "Next", show=True),
    ]

    def __init__(self, points: list[dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.points = points
        self.scope = "code"
        self._selected_id: str | None = points[0]["id"] if points else None
        self._preview = False
        self._want_file_pick = True

    def compose(self) -> ComposeResult:
        yield Label(
            "Rewind — 点击列表选点 · 点按钮设 scope · Enter/下一步",
            classes="modal-title",
            id="rewind-title",
        )
        yield Static("", id="rewind-scope", classes="modal-status")
        items = []
        for i, p in enumerate(self.points):
            label = (
                f"{i:>2}  {(p.get('id') or '')[:16]}  [{p.get('kind')}]  "
                f"files={p.get('file_count', 0)}  {(p.get('label') or '')[:48]}"
            )
            items.append(ListItem(Label(label), id=f"rw_{i}"))
        yield ListView(*items, id="rewind-list")
        with Horizontal(classes="modal-btns", id="rewind-scope-btns"):
            yield Button("Code 1", id="rw-scope-code")
            yield Button("Chat 2", id="rw-scope-conv")
            yield Button("Both 3", id="rw-scope-both")
            yield Button("Preview p", id="rw-preview")
            yield Button("文件勾选 f", id="rw-files")
        with Horizontal(classes="modal-btns"):
            yield Button("下一步 Next", variant="primary", id="rw-next")
            yield Button("取消 Esc", id="rw-cancel")
        self.call_after_refresh(self._refresh_scope_label)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        iid = event.item.id or ""
        if iid.startswith("rw_"):
            try:
                idx = int(iid[3:])
                self._selected_id = self.points[idx]["id"]
                self._refresh_scope_label()
            except (ValueError, IndexError):
                pass

    def on_list_view_highlighted(self, event: Any) -> None:
        try:
            lv = self.query_one("#rewind-list", ListView)
            idx = lv.index
            if idx is not None and 0 <= idx < len(self.points):
                self._selected_id = self.points[idx]["id"]
                self._refresh_scope_label()
        except Exception:
            pass

    def _refresh_scope_label(self) -> None:
        try:
            fp = "on" if self._want_file_pick and self.scope in ("code", "both") else "off"
            pv = "ON" if self._preview else "off"
            self.query_one("#rewind-scope", Static).update(
                f"scope=[bold cyan]{self.scope}[/]  preview=[bold]{pv}[/]  "
                f"file-pick={fp}  id={(self._selected_id or '-')[:18]}"
            )
            # light visual on active scope buttons
            for bid, sc in (
                ("rw-scope-code", "code"),
                ("rw-scope-conv", "conversation"),
                ("rw-scope-both", "both"),
            ):
                try:
                    b = self.query_one(f"#{bid}", Button)
                    b.variant = "primary" if self.scope == sc else "default"
                except Exception:
                    pass
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "rw-scope-code":
            self.action_scope_code()
        elif bid == "rw-scope-conv":
            self.action_scope_conv()
        elif bid == "rw-scope-both":
            self.action_scope_both()
        elif bid == "rw-preview":
            self.action_preview()
        elif bid == "rw-files":
            self.action_files_mode()
        elif bid == "rw-next":
            self.action_confirm()
        elif bid == "rw-cancel":
            self.action_cancel()

    def action_scope_code(self) -> None:
        self.scope = "code"
        self._refresh_scope_label()

    def action_scope_conv(self) -> None:
        self.scope = "conversation"
        self._want_file_pick = False
        self._refresh_scope_label()

    def action_scope_both(self) -> None:
        self.scope = "both"
        self._refresh_scope_label()

    def action_preview(self) -> None:
        self._preview = not self._preview
        self._refresh_scope_label()

    def action_files_mode(self) -> None:
        self._want_file_pick = not self._want_file_pick
        self._refresh_scope_label()

    def action_confirm(self) -> None:
        try:
            lv = self.query_one("#rewind-list", ListView)
            idx = lv.index
            if idx is not None and 0 <= idx < len(self.points):
                self._selected_id = self.points[idx]["id"]
        except Exception:
            pass
        if not self._selected_id:
            self.dismiss(None)
            return
        self.dismiss(
            {
                "point_id": self._selected_id,
                "scope": self.scope,
                "preview": self._preview,
                "file_pick": bool(self._want_file_pick and self.scope in ("code", "both")),
            }
        )

    def action_cancel(self) -> None:
        self.dismiss(None)


class FilePickScreen(ModalScreen[list[str] | None]):
    """Partial rewind — mouse checkboxes + buttons + keyboard."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("space", "toggle", "Toggle", show=True),
        Binding("a", "all", "All", show=True),
        Binding("n", "none", "None", show=True),
        Binding("enter", "confirm", "Rewind", show=True),
    ]

    def __init__(self, files: list[dict[str, Any]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.files = files
        self.selected: set[int] = {
            i for i, f in enumerate(files) if f.get("status") in ("restore", "delete", "create")
        }
        if not self.selected:
            self.selected = set(range(len(files)))

    def compose(self) -> ComposeResult:
        yield Label(
            "Partial rewind — 鼠标点复选框 · 底部按钮 · Space/a/n/Enter",
            classes="modal-title",
            id="fpick-title",
        )
        yield Static("", id="fpick-status", classes="modal-status")
        with VerticalScroll(id="fpick-scroll", classes="modal-scroll"):
            for i, f in enumerate(self.files):
                yield Checkbox(
                    f"[{f.get('status')}] {f.get('path')}",
                    value=i in self.selected,
                    id=f"fcb_{i}",
                )
        with Horizontal(id="fpick-btns", classes="modal-btns"):
            yield Button("全选 All", id="fpick-all")
            yield Button("清空 None", id="fpick-none")
            yield Button("应用回滚 Apply", variant="primary", id="fpick-ok")
            yield Button("取消 Esc", id="fpick-cancel")
        self.call_after_refresh(self._refresh_status)

    def _sync_from_checkboxes(self) -> None:
        sel: set[int] = set()
        for i in range(len(self.files)):
            try:
                cb = self.query_one(f"#fcb_{i}", Checkbox)
                if cb.value:
                    sel.add(i)
            except Exception:
                pass
        self.selected = sel

    def _refresh_status(self) -> None:
        self._sync_from_checkboxes()
        try:
            self.query_one("#fpick-status", Static).update(
                f"已选 {len(self.selected)}/{len(self.files)}  ·  点击复选框切换"
            )
        except Exception:
            pass

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        self._refresh_status()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "fpick-all":
            self.action_all()
        elif bid == "fpick-none":
            self.action_none()
        elif bid == "fpick-ok":
            self.action_confirm()
        elif bid == "fpick-cancel":
            self.action_cancel()

    def _set_all(self, value: bool) -> None:
        for i in range(len(self.files)):
            try:
                self.query_one(f"#fcb_{i}", Checkbox).value = value
            except Exception:
                pass
        self._refresh_status()

    def action_toggle(self) -> None:
        try:
            focused = self.focused
            if isinstance(focused, Checkbox) and focused.id and focused.id.startswith("fcb_"):
                focused.value = not focused.value
                self._refresh_status()
                return
            if self.files:
                cb = self.query_one("#fcb_0", Checkbox)
                cb.value = not cb.value
                self._refresh_status()
        except Exception:
            pass

    def action_all(self) -> None:
        self._set_all(True)

    def action_none(self) -> None:
        self._set_all(False)

    def action_confirm(self) -> None:
        self._sync_from_checkboxes()
        if not self.selected:
            self.dismiss([])
            return
        paths = [str(self.files[i].get("path") or "") for i in sorted(self.selected)]
        self.dismiss([p for p in paths if p])

    def action_cancel(self) -> None:
        self.dismiss(None)


class HunkPickScreen(ModalScreen[dict[str, Any] | None]):
    """Interactive hunk workbench — multi-file, checkboxes, colored preview.

    files: list of {path, patch, hunks}
    dismiss: {"applies": [{"path", "indices", "patch"}], ...} or None
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("space", "toggle", "Toggle", show=True),
        Binding("a", "all", "All", show=True),
        Binding("n", "none", "None", show=True),
        Binding("enter", "confirm", "Apply", show=True),
        Binding("j", "preview_next", "Next hunk", show=False),
        Binding("k", "preview_prev", "Prev hunk", show=False),
        Binding("right", "next_file", "Next file", show=False),
        Binding("left", "prev_file", "Prev file", show=False),
        Binding("tab", "next_file", "Next file", show=False),
    ]

    def __init__(self, files: list[dict[str, Any]], **kwargs: Any) -> None:
        """files: [{path, patch, hunks: list[Hunk]}]."""
        super().__init__(**kwargs)
        self.files = files
        self.file_idx = 0
        # selected hunk indices per file path
        self.selected_map: dict[str, set[int]] = {}
        for f in files:
            path = str(f.get("path") or "")
            hunks = f.get("hunks") or []
            self.selected_map[path] = set(range(len(hunks)))
        self._preview_hunk = 0

    @property
    def cur(self) -> dict[str, Any]:
        return self.files[self.file_idx]

    @property
    def cur_path(self) -> str:
        return str(self.cur.get("path") or "")

    @property
    def cur_hunks(self) -> list[Any]:
        return list(self.cur.get("hunks") or [])

    def compose(self) -> ComposeResult:
        yield Label("Hunk 工作台 — 点文件 · 勾 hunk · 右侧预览 · Apply", classes="modal-title")
        yield Static("", id="hpick-status", classes="modal-status")
        with Horizontal(id="hpick-body"):
            with Vertical(id="hpick-left"):
                yield Label("文件", id="hpick-files-title")
                with VerticalScroll(id="hpick-files"):
                    for i, f in enumerate(self.files):
                        path = str(f.get("path") or "?")
                        nh = len(f.get("hunks") or [])
                        yield Button(
                            f"{'▶ ' if i == 0 else '  '}{path} ({nh})",
                            id=f"hfile_{i}",
                            classes="hfile-btn",
                        )
            with Vertical(id="hpick-mid"):
                yield Label("Hunks", id="hpick-hunks-title")
                with VerticalScroll(id="hpick-scroll", classes="modal-scroll"):
                    # placeholders rebuilt on mount/switch
                    yield Static("(loading)", id="hpick-hunk-host")
            with Vertical(id="hpick-right"):
                yield Label("预览 (j/k 切换)", id="hpick-prev-title")
                yield RichLog(id="hpick-preview", highlight=True, markup=True, wrap=True)
        with Horizontal(id="hpick-btns", classes="modal-btns"):
            yield Button("全选", id="hpick-all")
            yield Button("清空", id="hpick-none")
            yield Button("应用此文件", id="hpick-ok-one")
            yield Button("应用全部已选", variant="primary", id="hpick-ok-all")
            yield Button("取消", id="hpick-cancel")
        self.call_after_refresh(self._rebuild_hunks)

    def _rebuild_hunks(self) -> None:
        """Rebuild checkbox list for current file."""
        try:
            scroll = self.query_one("#hpick-scroll", VerticalScroll)
            scroll.remove_children()
            path = self.cur_path
            hunks = self.cur_hunks
            sel = self.selected_map.setdefault(path, set(range(len(hunks))))
            for i, h in enumerate(hunks):
                header = h.header_line() if hasattr(h, "header_line") else str(h)
                plus = sum(1 for ln in getattr(h, "lines", []) if ln.startswith("+"))
                minus = sum(1 for ln in getattr(h, "lines", []) if ln.startswith("-"))
                scroll.mount(
                    Checkbox(
                        f"[{i}] {header}  +{plus} -{minus}",
                        value=i in sel,
                        id=f"hcb_{i}",
                    )
                )
            for i in range(len(self.files)):
                try:
                    b = self.query_one(f"#hfile_{i}", Button)
                    path_i = str(self.files[i].get("path") or "")
                    nh = len(self.files[i].get("hunks") or [])
                    nsel = len(self.selected_map.get(path_i, set()))
                    mark = "▶ " if i == self.file_idx else "  "
                    b.label = f"{mark}{path_i} ({nsel}/{nh})"
                    b.variant = "primary" if i == self.file_idx else "default"
                except Exception:
                    pass
            self._preview_hunk = min(self._preview_hunk, max(0, len(hunks) - 1)) if hunks else 0
            self._paint_preview()
            self._refresh_status()
        except Exception:
            pass

    def _sync_checkboxes(self) -> None:
        path = self.cur_path
        hunks = self.cur_hunks
        sel: set[int] = set()
        for i in range(len(hunks)):
            try:
                if self.query_one(f"#hcb_{i}", Checkbox).value:
                    sel.add(i)
            except Exception:
                pass
        self.selected_map[path] = sel

    def _refresh_status(self) -> None:
        self._sync_checkboxes()
        total_h = sum(len(f.get("hunks") or []) for f in self.files)
        total_s = sum(len(s) for s in self.selected_map.values())
        try:
            self.query_one("#hpick-status", Static).update(
                f"文件 {self.file_idx + 1}/{len(self.files)}  {self.cur_path}  ·  "
                f"本文件已选 {len(self.selected_map.get(self.cur_path, set()))}/{len(self.cur_hunks)}  ·  "
                f"全局 {total_s}/{total_h}  ·  ←/→ 换文件 · j/k 预览"
            )
        except Exception:
            pass

    def _paint_preview(self) -> None:
        try:
            log = self.query_one("#hpick-preview", RichLog)
            log.clear()
            hunks = self.cur_hunks
            if not hunks:
                log.write("(no hunks)")
                return
            i = max(0, min(self._preview_hunk, len(hunks) - 1))
            h = hunks[i]
            header = h.header_line() if hasattr(h, "header_line") else f"hunk {i}"
            checked = "✓" if i in self.selected_map.get(self.cur_path, set()) else " "
            log.write(Text.from_markup(f"[bold cyan][{checked}] hunk {i}/{len(hunks) - 1}[/] {escape(header)}"))
            for ln in list(getattr(h, "lines", []))[:80]:
                s = ln.rstrip("\n")
                if s.startswith("+"):
                    log.write(Text.from_markup(f"[green]{escape(s)}[/]"))
                elif s.startswith("-"):
                    log.write(Text.from_markup(f"[red]{escape(s)}[/]"))
                elif s.startswith("@@"):
                    log.write(Text.from_markup(f"[magenta]{escape(s)}[/]"))
                else:
                    log.write(Text.from_markup(f"[dim]{escape(s)}[/]"))
        except Exception:
            pass

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        self._sync_checkboxes()
        # if this checkbox id maps to preview index, refresh
        cid = event.checkbox.id or ""
        if cid.startswith("hcb_"):
            try:
                self._preview_hunk = int(cid[4:])
            except ValueError:
                pass
        self._paint_preview()
        self._refresh_status()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid.startswith("hfile_"):
            try:
                self._sync_checkboxes()
                self.file_idx = int(bid.split("_", 1)[1])
                self._preview_hunk = 0
                self._rebuild_hunks()
            except ValueError:
                pass
            return
        if bid == "hpick-all":
            self.action_all()
        elif bid == "hpick-none":
            self.action_none()
        elif bid == "hpick-ok-one":
            self.action_confirm_one()
        elif bid == "hpick-ok-all":
            self.action_confirm()
        elif bid == "hpick-cancel":
            self.action_cancel()

    def action_toggle(self) -> None:
        try:
            focused = self.focused
            if isinstance(focused, Checkbox):
                focused.value = not focused.value
                self._sync_checkboxes()
                self._paint_preview()
                self._refresh_status()
                return
            # toggle preview hunk
            i = self._preview_hunk
            path = self.cur_path
            sel = self.selected_map.setdefault(path, set())
            if i in sel:
                sel.discard(i)
            else:
                sel.add(i)
            try:
                self.query_one(f"#hcb_{i}", Checkbox).value = i in sel
            except Exception:
                pass
            self._paint_preview()
            self._refresh_status()
        except Exception:
            pass

    def action_all(self) -> None:
        path = self.cur_path
        self.selected_map[path] = set(range(len(self.cur_hunks)))
        for i in range(len(self.cur_hunks)):
            try:
                self.query_one(f"#hcb_{i}", Checkbox).value = True
            except Exception:
                pass
        self._paint_preview()
        self._refresh_status()

    def action_none(self) -> None:
        path = self.cur_path
        self.selected_map[path] = set()
        for i in range(len(self.cur_hunks)):
            try:
                self.query_one(f"#hcb_{i}", Checkbox).value = False
            except Exception:
                pass
        self._paint_preview()
        self._refresh_status()

    def action_preview_next(self) -> None:
        n = len(self.cur_hunks)
        if n:
            self._preview_hunk = (self._preview_hunk + 1) % n
            self._paint_preview()

    def action_preview_prev(self) -> None:
        n = len(self.cur_hunks)
        if n:
            self._preview_hunk = (self._preview_hunk - 1) % n
            self._paint_preview()

    def action_next_file(self) -> None:
        if len(self.files) < 2:
            return
        self._sync_checkboxes()
        self.file_idx = (self.file_idx + 1) % len(self.files)
        self._preview_hunk = 0
        self._rebuild_hunks()

    def action_prev_file(self) -> None:
        if len(self.files) < 2:
            return
        self._sync_checkboxes()
        self.file_idx = (self.file_idx - 1) % len(self.files)
        self._preview_hunk = 0
        self._rebuild_hunks()

    def _collect_applies(self, only_current: bool = False) -> list[dict[str, Any]]:
        self._sync_checkboxes()
        out: list[dict[str, Any]] = []
        indices = [self.file_idx] if only_current else list(range(len(self.files)))
        for i in indices:
            f = self.files[i]
            path = str(f.get("path") or "")
            idxs = sorted(self.selected_map.get(path, set()))
            if not idxs:
                continue
            out.append({"path": path, "indices": idxs, "patch": f.get("patch") or ""})
        return out

    def action_confirm_one(self) -> None:
        applies = self._collect_applies(only_current=True)
        if not applies:
            self.dismiss({"applies": []})
            return
        self.dismiss({"applies": applies})

    def action_confirm(self) -> None:
        applies = self._collect_applies(only_current=False)
        self.dismiss({"applies": applies})

    def action_cancel(self) -> None:
        self.dismiss(None)


class ModePickScreen(ModalScreen[str | None]):
    """Click mode buttons."""

    BINDINGS = [
        Binding("escape", "cancel", "Close"),
        Binding("1", "pick_build", "Build"),
        Binding("2", "pick_plan", "Plan"),
        Binding("3", "pick_always", "Always"),
        Binding("4", "pick_ask", "Ask"),
        Binding("5", "pick_explore", "Explore"),
    ]

    MODES = ("build", "plan", "always", "ask", "explore")

    def __init__(self, current: str = "build", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.current = current

    def compose(self) -> ComposeResult:
        yield Label(
            f"Mode — 当前 [bold]{self.current}[/]  ·  点击切换",
            classes="modal-title",
        )
        with Vertical(classes="modal-btns"):
            for m in self.MODES:
                v = "primary" if m == self.current else "default"
                yield Button(f"{m}", variant=v, id=f"mode-{m}")
            yield Button("取消", id="mode-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid.startswith("mode-") and bid != "mode-cancel":
            self.dismiss(bid[5:])
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_pick_build(self) -> None:
        self.dismiss("build")

    def action_pick_plan(self) -> None:
        self.dismiss("plan")

    def action_pick_always(self) -> None:
        self.dismiss("always")

    def action_pick_ask(self) -> None:
        self.dismiss("ask")

    def action_pick_explore(self) -> None:
        self.dismiss("explore")


class SlashPickScreen(ModalScreen[str | None]):
    """Click a slash command to insert."""

    BINDINGS = [Binding("escape", "cancel", "Close"), Binding("enter", "confirm", "Pick")]

    def __init__(self, hits: list[tuple[str, str]], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.hits = hits
        self._sel = hits[0][0] if hits else None

    def compose(self) -> ComposeResult:
        yield Label("Slash — 点击命令插入输入框", classes="modal-title")
        items = []
        for i, (cmd, desc) in enumerate(self.hits):
            items.append(ListItem(Label(f"{cmd}  —  {desc}"), id=f"sl_{i}"))
        yield ListView(*items, id="slash-list")
        with Horizontal(classes="modal-btns"):
            yield Button("插入", variant="primary", id="sl-ok")
            yield Button("取消", id="sl-cancel")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        iid = event.item.id or ""
        if iid.startswith("sl_"):
            try:
                self._sel = self.hits[int(iid[3:])][0]
            except (ValueError, IndexError):
                pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sl-ok":
            self.action_confirm()
        else:
            self.action_cancel()

    def action_confirm(self) -> None:
        try:
            lv = self.query_one("#slash-list", ListView)
            if lv.index is not None and 0 <= lv.index < len(self.hits):
                self._sel = self.hits[lv.index][0]
        except Exception:
            pass
        self.dismiss(self._sel)

    def action_cancel(self) -> None:
        self.dismiss(None)


class CommandPaletteScreen(ModalScreen[str | None]):
    """Fuzzy-ish command palette (Ctrl+K) — mouse + keyboard."""

    BINDINGS = [
        Binding("escape", "cancel", "Close"),
        Binding("enter", "confirm", "Run", show=True),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        from takton_code.tui.vim_ux import PALETTE_ACTIONS

        self._all = list(PALETTE_ACTIONS)
        self._hits = list(PALETTE_ACTIONS)
        self._sel = self._hits[0].id if self._hits else None

    def compose(self) -> ComposeResult:
        yield Label("Command Palette — 输入过滤 · ↑↓ · Enter · 点击", classes="modal-title")
        yield Input(placeholder="filter…", id="pal-filter")
        yield Static("", id="pal-status", classes="modal-status")
        yield ListView(id="pal-list")
        with Horizontal(classes="modal-btns"):
            yield Button("执行", variant="primary", id="pal-ok")
            yield Button("取消", id="pal-cancel")
        self.call_after_refresh(self._rebuild)

    def _rebuild(self) -> None:
        from takton_code.tui.vim_ux import filter_palette

        try:
            q = self.query_one("#pal-filter", Input).value or ""
        except Exception:
            q = ""
        self._hits = filter_palette(q)
        try:
            lv = self.query_one("#pal-list", ListView)
            lv.clear()
            for i, a in enumerate(self._hits):
                label = f"{a.label}  [{a.keys}]" if a.keys else a.label
                lv.append(ListItem(Label(f"{a.category}: {label}"), id=f"pa_{i}"))
            self.query_one("#pal-status", Static).update(f"{len(self._hits)} actions")
            if self._hits:
                self._sel = self._hits[0].id
        except Exception:
            pass

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "pal-filter":
            self._rebuild()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        iid = event.item.id or ""
        if iid.startswith("pa_"):
            try:
                self._sel = self._hits[int(iid[3:])].id
            except (ValueError, IndexError):
                pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "pal-ok":
            self.action_confirm()
        else:
            self.action_cancel()

    def action_confirm(self) -> None:
        try:
            lv = self.query_one("#pal-list", ListView)
            if lv.index is not None and 0 <= lv.index < len(self._hits):
                self._sel = self._hits[lv.index].id
        except Exception:
            pass
        self.dismiss(self._sel)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TaktonCodeApp(App[int]):
    ENABLE_COMMAND_PALETTE = False

    CSS = (
        """
    Screen { background: #0d1117; }
    #body { height: 1fr; }
    #chat {
        width: 3fr;
        border: solid #30363d;
        background: #0d1117;
    }
    #chat.-vim-focus { border: solid #a371f7; }
    #side {
        width: 1fr;
        border: solid #30363d;
        background: #161b22;
        padding: 0 1;
    }
    #side.-vim-focus { border: solid #a371f7; }
    #side-title { text-style: bold; color: #a371f7; padding: 1 0 0 0; }
    #stream {
        height: 3;
        border: solid #30363d;
        background: #0d1117;
        color: #e6edf3;
        padding: 0 1;
    }
    #toolbar {
        height: auto;
        padding: 0 1;
        background: #161b22;
        border-bottom: solid #30363d;
    }
    #toolbar Button {
        margin-right: 1;
        min-width: 8;
    }
    #perm-bar {
        height: auto;
        padding: 0 1;
        background: #3d2e00;
        border: solid #d29922;
        display: none;
    }
    #perm-bar.-show { display: block; }
    #perm-bar Button { margin-right: 1; }
    #perm-label { color: #e3b341; padding: 0 1; width: 1fr; }
    #input-row {
        dock: bottom;
        height: auto;
        background: #161b22;
        padding: 0 1 0 1;
    }
    #prompt { width: 1fr; border: tall #7c3aed; }
    #prompt.-normal-dim { border: tall #30363d; }
    #vim-search-row {
        dock: bottom;
        height: auto;
        background: #1a1020;
        padding: 0 1;
        display: none;
    }
    #vim-search-row.-show { display: block; }
    #vim-search { width: 1fr; border: tall #e3b341; }
    #status {
        dock: bottom;
        height: 1;
        background: #21262d;
        color: #8b949e;
    }
    #hpick-body { height: 1fr; }
    #hpick-left { width: 24; border: solid #30363d; padding: 0 1; }
    #hpick-mid { width: 2fr; }
    #hpick-right { width: 2fr; border: solid #30363d; }
    #hpick-preview { height: 1fr; background: #0d1117; }
    #hpick-files { height: 1fr; }
    .hfile-btn { width: 100%; margin-bottom: 1; }
    #vim-badge {
        dock: bottom;
        height: 1;
        background: #1a2332;
        color: #7ee787;
        padding: 0 1;
    }
    """
        + _MODAL_CSS
    )

    BINDINGS = [
        Binding("ctrl+c", "interrupt", "Stop", show=True, priority=True),
        Binding("ctrl+d", "quit", "Quit", show=True),
        Binding("ctrl+l", "clear_view", "Clear", show=False),
        Binding("f2", "toggle_side", "Side", show=True),
        Binding("tab", "toggle_mode", "Mode", show=True),
        Binding("shift+tab", "toggle_mode", "Mode", show=False),
        Binding("ctrl+o", "show_diff", "Diff", show=True),
        Binding("ctrl+semicolon", "show_queue", "Queue", show=True),
        Binding("ctrl+apostrophe", "show_queue", "Queue", show=False),
        Binding("ctrl+backslash", "dashboard", "Dash", show=True),
        Binding("ctrl+r", "open_rewind", "Rewind", show=True),
        Binding("escape", "esc_key", "Esc", show=False),
        Binding("right_square_bracket", "patch_next", "Patch+", show=False),
        Binding("left_square_bracket", "patch_prev", "Patch-", show=False),
        Binding("ctrl+shift+z", "unrewind", "Unrewind", show=True),
        Binding("ctrl+p", "open_slash", "Slash", show=True),
        Binding("ctrl+k", "open_palette", "Palette", show=True),
        Binding("y", "perm_allow", "Allow", show=False),
        Binding("n", "perm_deny", "Deny", show=False),
        Binding("a", "perm_always", "Always", show=False),
    ]

    def __init__(
        self,
        runtime: AgentRuntime,
        *,
        hub: SessionHub | None = None,
        open_session_cb: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.runtime = runtime
        self.hub = hub
        self.open_session_cb = open_session_cb
        self._side_visible = True
        self._esc_armed = False
        self._perm_pending: dict[str, Any] | None = None
        self._last_assistant: str = ""
        self._chat_lines: list[str] = []
        self._side_lines: list[str] = []
        self._vim_enabled = True
        self._palette_enabled = True
        from takton_code.tui.vim_ux import VimState

        self._vim = VimState()
        ui_flush_chars, ui_flush_ms = 1, 16
        try:
            from takton_code.config import apply_settings_json, load_settings

            ui = apply_settings_json(load_settings()).ui
            ui_flush_chars = int(ui.stream_flush_chars)
            ui_flush_ms = int(ui.stream_flush_ms)
            self._vim_enabled = bool(getattr(ui, "vim_keys", True))
            self._palette_enabled = bool(getattr(ui, "command_palette", True))
        except Exception:
            pass
        self._stream_buf = StreamBuffer(flush_chars=ui_flush_chars, flush_ms=ui_flush_ms)
        self._stream_text = ""
        self.title = "Takton Code"
        self.sub_title = str(runtime.project.root)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="toolbar"):
            yield Button("Mode", id="tb-mode")
            yield Button("Rewind", id="tb-rewind")
            yield Button("Hunks", id="tb-hunks")
            yield Button("Unrewind", id="tb-unrewind")
            yield Button("Diff", id="tb-diff")
            yield Button("Queue", id="tb-queue")
            yield Button("Sessions", id="tb-dash")
            yield Button("Slash", id="tb-slash")
            yield Button("Palette", id="tb-palette")
            yield Button("Todos", id="tb-todos")
            yield Button("Side", id="tb-side")
            yield Button("Stop", id="tb-stop", variant="error")
        with Horizontal(id="perm-bar"):
            yield Static("", id="perm-label")
            yield Button("Allow y", variant="success", id="perm-allow")
            yield Button("Deny n", variant="error", id="perm-deny")
            yield Button("Always a", id="perm-always")
        with Horizontal(id="body"):
            with Vertical():
                yield RichLog(id="chat", highlight=True, markup=True, wrap=True, auto_scroll=True)
                yield Static("", id="stream")
            with Vertical(id="side"):
                yield Label("Changes · Plan · Queue · /slash", id="side-title")
                yield RichLog(id="side-log", highlight=True, markup=True, wrap=True, auto_scroll=True)
        with Vertical(id="input-row"):
            yield Input(
                placeholder="INSERT · Esc=NORMAL · /=搜索 · 10j · Ctrl+K 面板 · yy 复制",
                id="prompt",
            )
        with Horizontal(id="vim-search-row"):
            yield Label("/", id="vim-search-label")
            yield Input(placeholder="pattern · Enter 跳转 · Esc 取消 · n/N 下一个", id="vim-search")
        yield Static("INSERT", id="vim-badge")
        yield StatusBar(id="status")
        yield Footer()

    def on_mount(self) -> None:
        chat = self.query_one("#chat", RichLog)
        chat.write(
            Text.from_markup(
                f"[bold #a371f7]Takton Code[/]  ·  mouse + keyboard + vim\n"
                f"[dim]project[/] {escape(str(self.runtime.project.root))}\n"
                f"[dim]session[/] {self.runtime.session_id}\n"
                f"[dim]model[/]   {escape(str(self.runtime.llm_snapshot.get('model')))}\n"
                f"[dim]tip[/] Esc NORMAL · i INSERT · /search · 10j · n/N · yy · Ctrl+K\n"
            )
        )
        self.query_one("#prompt", Input).focus()
        self._vim.enter_insert()
        self._refresh_vim_badge()
        self.set_interval(0.8, self._refresh_status)

    def _refresh_vim_badge(self) -> None:
        try:
            badge = self.query_one("#vim-badge", Static)
            if not self._vim_enabled:
                badge.update("vim:off")
                return
            badge.update(self._vim.label())
            prompt = self.query_one("#prompt", Input)
            chat = self.query_one("#chat", RichLog)
            side = self.query_one("#side")
            if self._vim.mode == "normal":
                prompt.add_class("-normal-dim")
                if self._vim.focus == "chat":
                    chat.add_class("-vim-focus")
                    side.remove_class("-vim-focus")
                else:
                    side.add_class("-vim-focus")
                    chat.remove_class("-vim-focus")
            else:
                prompt.remove_class("-normal-dim")
                chat.remove_class("-vim-focus")
                side.remove_class("-vim-focus")
        except Exception:
            pass

    # ── toolbar / perm mouse ───────────────────────────────────────────────
    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "tb-mode":
            self.action_pick_mode()
        elif bid == "tb-rewind":
            self.action_open_rewind()
        elif bid == "tb-hunks":
            self.action_open_hunks()
        elif bid == "tb-unrewind":
            self.action_unrewind()
        elif bid == "tb-diff":
            self.action_show_diff()
        elif bid == "tb-queue":
            self.action_show_queue()
        elif bid == "tb-dash":
            self.action_dashboard()
        elif bid == "tb-slash":
            self.action_open_slash()
        elif bid == "tb-palette":
            self.action_open_palette()
        elif bid == "tb-todos":
            asyncio.create_task(self._show_todos())
        elif bid == "tb-side":
            self.action_toggle_side()
        elif bid == "tb-stop":
            self.action_interrupt()
        elif bid == "perm-allow":
            self.action_perm_allow()
        elif bid == "perm-deny":
            self.action_perm_deny()
        elif bid == "perm-always":
            self.action_perm_always()

    def action_open_palette(self) -> None:
        if not self._palette_enabled:
            return
        asyncio.create_task(self._open_palette())

    async def _open_palette(self) -> None:
        choice = await self.push_screen_wait(CommandPaletteScreen())
        if not choice:
            return
        await self._run_palette_action(choice)

    async def _run_palette_action(self, action_id: str) -> None:
        chat = self.query_one("#chat", RichLog)
        mapping = {
            "mode": self.action_pick_mode,
            "rewind": self.action_open_rewind,
            "hunks": self.action_open_hunks,
            "unrewind": self.action_unrewind,
            "diff": self.action_show_diff,
            "queue": self.action_show_queue,
            "todos": lambda: asyncio.create_task(self._show_todos()),
            "sessions": self.action_dashboard,
            "slash": self.action_open_slash,
            "side": self.action_toggle_side,
            "stop": self.action_interrupt,
            "clear": self.action_clear_view,
            "yank": self.action_yank_last,
            "insert": self.action_enter_insert,
            "vim_help": self.action_vim_help,
        }
        if action_id in mapping:
            mapping[action_id]()
            return
        # slash-backed
        if action_id == "compact":
            r = await self.runtime._handle_slash("/compact")
            if r:
                chat.write(Text.from_markup(f"[magenta]{escape(r.final_text or r.error or '')}[/]"))
        elif action_id == "export_md":
            r = await self.runtime._handle_slash("/export md")
            if r:
                chat.write(Text.from_markup(f"[yellow]{escape(r.final_text or '')}[/]"))
        elif action_id == "export_jsonl":
            r = await self.runtime._handle_slash("/export jsonl")
            if r:
                chat.write(Text.from_markup(f"[yellow]{escape(r.final_text or '')}[/]"))
        elif action_id == "fork":
            r = await self.runtime._handle_slash("/fork")
            if r:
                chat.write(Text.from_markup(f"[yellow]{escape(r.final_text or '')}[/]"))

    def action_enter_insert(self) -> None:
        self._vim.enter_insert()
        try:
            self.query_one("#prompt", Input).focus()
        except Exception:
            pass
        self._refresh_vim_badge()

    def action_enter_normal(self) -> None:
        if not self._vim_enabled:
            return
        self._vim.enter_normal()
        try:
            self.query_one("#chat", RichLog).focus()
        except Exception:
            pass
        self._refresh_vim_badge()

    def action_vim_help(self) -> None:
        chat = self.query_one("#chat", RichLog)
        chat.write(
            Text.from_markup(
                "[bold #a371f7]Vim NORMAL[/]\n"
                "[dim]i/a[/] INSERT  ·  [dim]Esc[/] NORMAL\n"
                "[dim]j/k[/] 滚动  ·  [dim]10j[/] 计数器  ·  [dim]Ctrl-d/u[/] 半页  ·  [dim]gg/G[/] 顶/底\n"
                "[dim]/[/] 搜索高亮  ·  [dim]n/N[/] 下/上一处  ·  [dim]w/e[/] side/chat\n"
                "[dim]yy[/] 复制回复  ·  [dim]R[/] Rewind  ·  [dim]H[/] Hunks  ·  [dim]:[/] 面板\n"
            )
        )

    def action_yank_last(self) -> None:
        text = self._last_assistant or ""
        if not text.strip():
            self.query_one("#chat", RichLog).write(Text.from_markup("[dim]nothing to yank[/]"))
            return
        ok = False
        try:
            import pyperclip  # type: ignore

            pyperclip.copy(text)
            ok = True
        except Exception:
            pass
        if not ok:
            try:
                # Windows fallback
                import subprocess

                subprocess.run(
                    ["clip"],
                    input=text.encode("utf-16le"),
                    check=False,
                    timeout=5,
                )
                ok = True
            except Exception:
                pass
        chat = self.query_one("#chat", RichLog)
        if ok:
            chat.write(Text.from_markup(f"[green]yanked[/] {len(text)} chars → clipboard"))
        else:
            # still show in side for manual copy
            side = self.query_one("#side-log", RichLog)
            side.clear()
            side.write(Text.from_markup("[bold]YANK (clipboard failed — select below)[/]"))
            side.write(text[:12000])
            chat.write(Text.from_markup("[yellow]clipboard failed — see side panel[/]"))

    def action_esc_key(self) -> None:
        """Esc: cancel search → INSERT→NORMAL; NORMAL→arm rewind."""
        if self._vim_enabled:
            if self._vim.mode == "search":
                self._close_search(cancel=True)
                return
            try:
                focused = self.focused
                if isinstance(focused, Input) and focused.id == "vim-search":
                    self._close_search(cancel=True)
                    return
                if isinstance(focused, Input) and focused.id == "prompt":
                    self.action_enter_normal()
                    return
            except Exception:
                pass
            if self._vim.mode == "insert":
                self.action_enter_normal()
                return
            if self._esc_armed:
                self._esc_armed = False
                asyncio.create_task(self._open_rewind())
            else:
                self._esc_armed = True
                self.set_timer(0.8, self._clear_esc)
            return
        self.action_esc_undo()

    def _open_search_ui(self) -> None:
        self._vim.enter_search()
        try:
            row = self.query_one("#vim-search-row")
            row.add_class("-show")
            inp = self.query_one("#vim-search", Input)
            inp.value = self._vim.search_query or ""
            inp.focus()
        except Exception:
            pass
        self._refresh_vim_badge()

    def _close_search(self, *, cancel: bool = False) -> None:
        try:
            row = self.query_one("#vim-search-row")
            row.remove_class("-show")
        except Exception:
            pass
        if cancel:
            # keep last query for n/N but leave normal
            pass
        self._vim.enter_normal()
        try:
            self.query_one("#chat", RichLog).focus()
        except Exception:
            pass
        self._refresh_vim_badge()

    @on(Input.Submitted, "#vim-search")
    def on_vim_search_submit(self, event: Input.Submitted) -> None:
        q = (event.value or "").strip()
        self._run_search(q)
        self._close_search(cancel=False)

    def _log_lines(self) -> list[str]:
        if self._vim.focus == "side":
            return list(self._side_lines)
        return list(self._chat_lines)

    def _run_search(self, query: str) -> None:
        from takton_code.tui.vim_ux import find_hits, highlight_line

        self._vim.search_query = query
        hits = find_hits(self._log_lines(), query)
        self._vim.set_hits(hits)
        chat = self.query_one("#chat", RichLog)
        if not query:
            chat.write(Text.from_markup("[dim]empty search[/]"))
            return
        if not hits:
            chat.write(Text.from_markup(f"[yellow]no match[/] /{escape(query)}"))
            self._refresh_vim_badge()
            return
        chat.write(
            Text.from_markup(
                f"[cyan]/{escape(query)}[/]  {len(hits)} hits  ·  n/N next  ·  focus={self._vim.focus}"
            )
        )
        self._show_search_hit(hits[0], is_current=True)
        self._refresh_vim_badge()

    def _show_search_hit(self, hit: Any, *, is_current: bool = True) -> None:
        from takton_code.tui.vim_ux import highlight_line

        side = self.query_one("#side-log", RichLog)
        # Preview matches in side panel with highlight
        lines = self._log_lines()
        q = self._vim.search_query
        side.clear()
        side.write(
            Text.from_markup(
                f"[bold #e3b341]SEARCH /{escape(q)}[/]  "
                f"{self._vim.search_idx + 1}/{len(self._vim.search_hits)}"
            )
        )
        # show window of lines around hit
        start = max(0, hit.line_index - 3)
        end = min(len(lines), hit.line_index + 8)
        for i in range(start, end):
            cur = i == hit.line_index
            mark = "▶" if cur else " "
            hl = highlight_line(lines[i], q, current=cur)
            side.write(Text.from_markup(f"{mark} {i:>4} {hl}"))
        # approximate scroll primary log toward hit
        try:
            wid = "#side-log" if self._vim.focus == "side" else "#chat"
            log = self.query_one(wid, RichLog)
            # scroll proportionally
            total = max(1, len(lines))
            frac = hit.line_index / total
            log.scroll_home(animate=False)
            # page down a few times based on fraction
            pages = int(frac * 20)
            for _ in range(pages):
                log.scroll_page_down(animate=False)
        except Exception:
            pass

    def _search_next(self, *, reverse: bool = False) -> None:
        if not self._vim.search_query:
            self._open_search_ui()
            return
        if not self._vim.search_hits:
            self._run_search(self._vim.search_query)
            return
        hit = self._vim.prev_hit() if reverse else self._vim.next_hit()
        if hit:
            self._show_search_hit(hit)
            self._refresh_vim_badge()

    def _track_line(self, target: str, text: str) -> None:
        plain = text.replace("\r", "")
        for line in plain.split("\n"):
            if target == "side":
                self._side_lines.append(line)
                if len(self._side_lines) > 5000:
                    self._side_lines = self._side_lines[-4000:]
            else:
                self._chat_lines.append(line)
                if len(self._chat_lines) > 8000:
                    self._chat_lines = self._chat_lines[-6000:]

    def on_key(self, event: Any) -> None:
        """Vim NORMAL key routing (only when not typing in Input)."""
        if not self._vim_enabled:
            return
        if self._vim.mode == "search":
            return  # input handles it
        if self._vim.mode != "normal":
            return
        if len(self.screen_stack) > 1:
            return
        try:
            if isinstance(self.focused, Input):
                return
        except Exception:
            pass
        if self._perm_pending:
            return
        key = getattr(event, "key", "") or ""
        char = getattr(event, "character", None) or ""

        # digits → count (except lone 0 = home)
        if char and char.isdigit():
            if char == "0" and not self._vim.count_str:
                self._scroll_log("home")
                event.prevent_default()
                event.stop()
                return
            self._vim.feed_digit(char)
            self._refresh_vim_badge()
            event.prevent_default()
            event.stop()
            return

        # multi-key pending
        if self._vim.pending == "g":
            self._vim.pending = ""
            if key == "g" or char == "g":
                n = self._vim.take_count(1)
                self._scroll_log("home")
                event.prevent_default()
                event.stop()
                self._refresh_vim_badge()
                return
            self._refresh_vim_badge()
        if self._vim.pending == "y":
            self._vim.pending = ""
            if key == "y" or char == "y":
                self.action_yank_last()
                event.prevent_default()
                event.stop()
                self._refresh_vim_badge()
                return
            self._refresh_vim_badge()

        handled = True
        count = 1  # may take_count inside branches

        if key in ("j",) or char == "j":
            count = self._vim.take_count(1)
            for _ in range(count):
                self._scroll_log("down")
        elif key in ("k",) or char == "k":
            count = self._vim.take_count(1)
            for _ in range(count):
                self._scroll_log("up")
        elif key == "ctrl+d":
            count = self._vim.take_count(1)
            for _ in range(count):
                self._scroll_log("pagedown")
        elif key == "ctrl+u":
            count = self._vim.take_count(1)
            for _ in range(count):
                self._scroll_log("pageup")
        elif key == "G" or char == "G":
            # 10G → jump toward end; bare G = end
            self._vim.take_count(1)
            self._scroll_log("end")
        elif key == "g" or char == "g":
            self._vim.pending = "g"
            self._refresh_vim_badge()
        elif key == "y" or char == "y":
            self._vim.pending = "y"
            self._refresh_vim_badge()
        elif key == "slash" or char == "/":
            self._vim.take_count(1)
            self._open_search_ui()
        elif key == "n" or char == "n":
            count = self._vim.take_count(1)
            for _ in range(count):
                self._search_next(reverse=False)
        elif key == "N" or char == "N":
            count = self._vim.take_count(1)
            for _ in range(count):
                self._search_next(reverse=True)
        elif key in ("i", "a") or char in ("i", "a", "I", "A"):
            self._vim.take_count(1)
            self.action_enter_insert()
        elif key == "w" or char == "w":
            self._vim.take_count(1)
            self._vim.focus = "side"
            try:
                self.query_one("#side-log", RichLog).focus()
            except Exception:
                pass
            self._refresh_vim_badge()
        elif key == "e" or char == "e":
            self._vim.take_count(1)
            self._vim.focus = "chat"
            try:
                self.query_one("#chat", RichLog).focus()
            except Exception:
                pass
            self._refresh_vim_badge()
        elif key == "R" or char == "R":
            self._vim.take_count(1)
            self.action_open_rewind()
        elif key == "H" or char == "H":
            self._vim.take_count(1)
            self.action_open_hunks()
        elif key == "u" or char == "u":
            self._vim.take_count(1)
            self.action_unrewind()
        elif key == "colon" or char == ":":
            self._vim.take_count(1)
            self.action_open_palette()
        elif key == "question_mark" or char == "?":
            self._vim.take_count(1)
            self.action_vim_help()
        else:
            handled = False
            # unknown key clears count
            if self._vim.count_str:
                self._vim.count_str = ""
                self._refresh_vim_badge()
        if handled:
            event.prevent_default()
            event.stop()
            self._refresh_vim_badge()

    def _scroll_log(self, how: str) -> None:
        try:
            wid = "#side-log" if self._vim.focus == "side" else "#chat"
            log = self.query_one(wid, RichLog)
            if how == "down":
                log.scroll_relative(y=1, animate=False)
            elif how == "up":
                log.scroll_relative(y=-1, animate=False)
            elif how == "pagedown":
                log.scroll_page_down(animate=False)
            elif how == "pageup":
                log.scroll_page_up(animate=False)
            elif how == "home":
                log.scroll_home(animate=False)
            elif how == "end":
                log.scroll_end(animate=False)
        except Exception:
            pass

    async def _show_todos(self) -> None:
        side = self.query_one("#side-log", RichLog)
        side.clear()
        side.write(Text.from_markup("[bold #a371f7]TODOS[/]"))
        try:
            rows = await self.runtime.store.list_todos(self.runtime.session_id or "")
        except Exception:
            rows = []
        if not rows:
            side.write("(empty — model can todo_write)")
            return
        for it in rows:
            st = str(it.get("status") or "pending")
            mark = {"done": "✓", "in_progress": "…", "pending": "○"}.get(st, "·")
            side.write(f"{mark} [{st}] {it.get('content')}")

    def _set_perm_bar(self, show: bool, text: str = "") -> None:
        try:
            bar = self.query_one("#perm-bar")
            if show:
                bar.add_class("-show")
                self.query_one("#perm-label", Static).update(text)
            else:
                bar.remove_class("-show")
                self.query_one("#perm-label", Static).update("")
        except Exception:
            pass

    def _stream_widget(self) -> Static:
        return self.query_one("#stream", Static)

    def _paint_stream(self, chunk: str) -> None:
        self._stream_text += chunk
        shown = self._stream_text[-2000:]
        self._stream_widget().update(Text(shown))

    def _commit_stream_to_chat(self) -> None:
        left = self._stream_buf.flush()
        if left:
            self._stream_text += left
        if self._stream_text.strip():
            chat = self.query_one("#chat", RichLog)
            chat.write(Text.from_markup(escape(self._stream_text)))
            self._track_line("chat", self._stream_text)
        self._stream_text = ""
        self._stream_widget().update("")

    def _refresh_status(self) -> None:
        try:
            tokens = {}
            if self.runtime.compressor:
                tokens = self.runtime.compressor.meter.status(self.runtime.messages)
            asyncio.create_task(self._refresh_status_async(tokens))
        except Exception:
            pass

    async def _refresh_status_async(self, tokens: dict) -> None:
        qn = 0
        slug = ""
        try:
            if hasattr(self.runtime, "context_meter"):
                tokens = self.runtime.context_meter()
        except Exception:
            pass
        try:
            if self.runtime.session_id:
                q = await self.runtime.store.list_queue(self.runtime.session_id)
                qn = len(q or [])
                row = await self.runtime.store.get_session(self.runtime.session_id)
                slug = (row or {}).get("slug") or ""
        except Exception:
            pass
        bar = self.query_one("#status", StatusBar)
        bar.update_status(
            {
                "mode": self.runtime.mode,
                "model": self.runtime.llm_snapshot.get("model"),
                "tokens": tokens,
                "compress_count": self.runtime.compressor.compress_count
                if self.runtime.compressor
                else 0,
                "bridge": bool(getattr(self.runtime.bridge, "enabled", False)),
                "plan_state": self.runtime.plan_gate.state.value,
                "session_id": self.runtime.session_id,
                "slug": slug,
                "queue_n": qn,
                "usage_totals": getattr(self.runtime, "usage_totals", {}),
                "perm_pending": bool(self._perm_pending),
                "thrashing": bool(
                    getattr(self.runtime, "thrashing", None) and self.runtime.thrashing.active
                ),
            }
        )

    def _handle_event(self, ev: dict[str, Any]) -> None:
        chat = self.query_one("#chat", RichLog)
        side = self.query_one("#side-log", RichLog)
        t = ev.get("type")

        if t == "permission_request":
            self._perm_pending = ev
            summary = str(ev.get("summary") or "")
            tool = str(ev.get("tool") or "")
            side.write(
                Text.from_markup(
                    f"[bold yellow]ASK[/] {escape(tool)}\n"
                    f"{escape(summary)}\n"
                    f"[dim]点顶部权限条 · 或按 y/n/a[/]"
                )
            )
            chat.write(
                Text.from_markup(
                    f"[yellow]permission:[/] {escape(tool)} — 点权限条 Allow/Deny/Always 或 y/n/a"
                )
            )
            self._set_perm_bar(True, f"⚠ ASK {tool}: {summary[:80]}")
            return

        if t == "todos":
            side.clear()
            side.write(Text.from_markup("[bold #a371f7]TODOS[/]"))
            items = ev.get("items") or []
            if not items:
                side.write("(empty)")
            for it in items:
                st = str(it.get("status") or "pending")
                mark = {"done": "✓", "in_progress": "…", "pending": "○"}.get(st, "·")
                side.write(f"{mark} [{st}] {it.get('content')}")
            return

        if t == "compress":
            chat.write(
                Text.from_markup(
                    f"[magenta]compress[/] {ev.get('before_tokens')}→{ev.get('after_tokens')} "
                    f"#{ev.get('count')} ({ev.get('reason')})"
                )
            )
            return

        if t == "subagent_start":
            side.write(
                Text.from_markup(
                    f"[bold magenta]SUBAGENT[/] {escape(str(ev.get('agent')))} "
                    f"{escape(str(ev.get('prompt') or '')[:80])}"
                )
            )
            return
        if t == "subagent_end":
            side.write(
                Text.from_markup(
                    f"[magenta]subagent done[/] chars={ev.get('chars')} ok={ev.get('ok')}"
                )
            )
            return

        if t == "image_attach":
            chat.write(Text.from_markup(f"[cyan]attached images:[/] {ev.get('count')}"))
            return

        if t in ("assistant", "text") and ev.get("text"):
            # accumulate streaming assistant if needed
            pass

        if t == "text_delta":
            piece = self._stream_buf.push(ev.get("text") or "")
            if piece:
                self._paint_stream(piece)
            return
        if t == "reasoning_delta":
            piece = (ev.get("text") or "")[:120].replace("\n", " ")
            if piece:
                chat.write(Text.from_markup(f"[dim italic]… {escape(piece)}[/]"))
            return

        if t not in ("usage", "tool_calls_delta"):
            snap = self._stream_text
            self._commit_stream_to_chat()
            if snap.strip():
                self._last_assistant = snap
            if t in ("turn_end", "assistant_final") and ev.get("text"):
                self._last_assistant = str(ev.get("text") or "")
            if t == "assistant_tools" and ev.get("content"):
                self._last_assistant = str(ev.get("content") or self._last_assistant)

        for line in format_event_lines(ev):
            if line.kind == "user":
                continue
            if line.kind == "assistant" and line.text:
                self._last_assistant = line.text
            color = {
                "tool": "#58a6ff",
                "reasoning": "dim italic",
                "system": "magenta",
                "status": "yellow",
                "permission": "yellow",
                "assistant": "",
            }.get(line.kind, "")
            if line.kind == "tool" and line.text.startswith("✓"):
                side.write(Text.from_markup(f"[green]{escape(line.text[:100])}[/]"))
                self._track_line("side", line.text[:100])
            if color:
                chat.write(Text.from_markup(f"[{color}]{escape(line.text)}[/]"))
            else:
                chat.write(Text.from_markup(escape(line.text)))
            self._track_line("chat", line.text)

        if t == "plan_ready":
            side.write(Text.from_markup("[bold magenta]PLAN READY[/] — /approve 或点 Slash"))
    @on(Input.Changed, "#prompt")
    def on_input_changed(self, event: Input.Changed) -> None:
        val = event.value or ""
        if not val.startswith("/"):
            return
        token = val.split()[0] if val.split() else val
        hits = filter_slash_commands(token)
        side = self.query_one("#side-log", RichLog)
        side.clear()
        side.write(Text.from_markup("[bold #a371f7]/ slash[/]  Ctrl+P 打开可点列表"))
        for cmd, desc in hits[:14]:
            side.write(Text.from_markup(f"[cyan]{escape(cmd)}[/]  [dim]{escape(desc)}[/]"))

    @on(Input.Submitted, "#prompt")
    def on_submit(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        event.input.value = ""
        self._esc_armed = False
        self._vim.enter_insert()
        self._refresh_vim_badge()
        if not text:
            return
        if text in ("/exit", "/quit", "exit", "quit"):
            self.exit(0)
            return
        if text == "/stop":
            self.action_interrupt()
            return
        if text in ("/hunk", "/hunks", "/hunk ui", "/hunk pick"):
            self.action_open_hunks()
            return
        if text in ("/allow",) or text.startswith("/allow "):
            self.action_perm_allow()
            return
        if text in ("/deny",) or text.startswith("/deny "):
            self.action_perm_deny()
            return

        if self.runtime._running:
            chat = self.query_one("#chat", RichLog)
            if text.startswith("/enqueue "):
                body = text[len("/enqueue ") :].strip()
                if body:
                    asyncio.create_task(self.runtime.enqueue(body))
                    chat.write(Text.from_markup(f"[cyan]⇢ queued:[/] {escape(body)}"))
                return
            if text.startswith("/") and not text.startswith("/steer"):
                chat.write(Text.from_markup("[dim]busy — plain text steers; /enqueue queues[/]"))
                return
            body = text[7:] if text.startswith("/steer ") else text
            self.runtime.steer(body)
            chat.write(Text.from_markup(f"[yellow]⇢ steer:[/] {escape(body)}"))
            return

        chat = self.query_one("#chat", RichLog)
        chat.write(Text.from_markup(f"\n[bold #e6edf3]› {escape(text)}[/]\n"))
        self.run_turn(text)

    @work(exclusive=True)
    async def run_turn(self, text: str) -> None:
        chat = self.query_one("#chat", RichLog)
        prev = self.runtime.on_event
        self._stream_text = ""
        self._stream_buf = StreamBuffer(
            flush_chars=self._stream_buf.flush_chars,
            flush_ms=self._stream_buf.flush_ms,
        )

        def combined(ev: dict[str, Any]) -> None:
            if prev:
                prev(ev)
            self._handle_event(ev)

        self.runtime.on_event = combined
        try:
            result = await self.runtime.run_turn(text)
        except Exception as e:  # noqa: BLE001
            self._commit_stream_to_chat()
            chat.write(Text.from_markup(f"[red]turn failed: {escape(str(e))}[/]"))
            return
        finally:
            self._commit_stream_to_chat()
            self.runtime.on_event = prev

        if result.error:
            chat.write(Text.from_markup(f"[red]{escape(result.error)}[/]"))
        if result.changes_summary and "no file changes" not in result.changes_summary:
            side = self.query_one("#side-log", RichLog)
            side.write(Text.from_markup("[bold green]CHANGES[/]"))
            side.write(result.changes_summary)
            chat.write(Text.from_markup(f"[green]{escape(result.changes_summary)}[/]"))
        if result.interrupted:
            chat.write(Text.from_markup("[yellow]Interrupted — /continue[/]"))
        try:
            nxt = await self.runtime.drain_queue_once()
            if nxt and nxt.final_text:
                chat.write(Text.from_markup(f"\n[cyan]queue drain ›[/]\n{escape(nxt.final_text)}"))
        except Exception:
            pass

    def action_interrupt(self) -> None:
        self.runtime.request_cancel()
        self.query_one("#chat", RichLog).write(Text.from_markup("[red]Stop → cancel[/]"))

    def action_toggle_mode(self) -> None:
        """Tab = cycle; toolbar Mode button calls action_pick_mode."""
        new = self.runtime.cycle_mode()
        asyncio.create_task(self._set_mode(new))

    def action_pick_mode(self) -> None:
        asyncio.create_task(self._pick_mode())

    async def _set_mode(self, mode: str) -> None:
        await self.runtime.set_mode(mode)
        self.query_one("#chat", RichLog).write(
            Text.from_markup(f"[magenta]mode → {mode}[/]  (Tab 循环 · 工具栏 Mode 点选)")
        )

    async def _pick_mode(self) -> None:
        choice = await self.push_screen_wait(ModePickScreen(self.runtime.mode))
        if not choice:
            return
        await self._set_mode(choice)

    def action_show_diff(self) -> None:
        if not self.runtime.diff:
            return
        d = self.runtime.diff.all_diffs()
        side = self.query_one("#side-log", RichLog)
        side.clear()
        side.write(Text.from_markup("[bold]DIFF[/]"))
        side.write(d[:14000] or "(no diffs)")

    def action_show_queue(self) -> None:
        asyncio.create_task(self._show_queue())

    async def _show_queue(self) -> None:
        q = await self.runtime.list_queue()
        side = self.query_one("#side-log", RichLog)
        side.write(Text.from_markup("[bold cyan]QUEUE[/]"))
        if not q:
            side.write("(empty)")
        for i in q:
            side.write(f"#{i['id']}: {i['content'][:80]}")

    def action_dashboard(self) -> None:
        asyncio.create_task(self._open_dashboard())

    async def _open_dashboard(self) -> None:
        rows: list[dict[str, Any]] = []
        if self.hub:
            rows = await self.hub.list_db_sessions(40)
            open_map = {x["id"]: x for x in self.hub.list_open()}
            for r in rows:
                if r["id"] in open_map:
                    r["open"] = True
                    r["running"] = open_map[r["id"]].get("running")
                    r["active"] = open_map[r["id"]].get("active")
        else:
            try:
                rows = await self.runtime.store.list_sessions(40)
                for r in rows:
                    r["open"] = r.get("id") == self.runtime.session_id
                    r["active"] = r.get("id") == self.runtime.session_id
                    r["running"] = bool(self.runtime._running) if r.get("active") else False
            except Exception:
                rows = []
        choice = await self.push_screen_wait(DashboardScreen(rows))
        if not choice or not self.open_session_cb:
            return
        try:
            rt = await self.open_session_cb(None if choice == "__new__" else choice)
            self.runtime = rt
            self.sub_title = str(rt.project.root)
            chat = self.query_one("#chat", RichLog)
            chat.write(
                Text.from_markup(f"[magenta]switched session[/] {rt.session_id} mode={rt.mode}")
            )
        except Exception as e:  # noqa: BLE001
            self.query_one("#chat", RichLog).write(
                Text.from_markup(f"[red]switch failed: {escape(str(e))}[/]")
            )

    def action_open_slash(self) -> None:
        asyncio.create_task(self._open_slash())

    async def _open_slash(self) -> None:
        from takton_code.agent.refs import SLASH_COMMANDS

        hits = list(SLASH_COMMANDS)
        # prefer current input filter
        try:
            val = self.query_one("#prompt", Input).value or ""
            if val.startswith("/"):
                hits = filter_slash_commands(val.split()[0])
        except Exception:
            pass
        if not hits:
            hits = list(SLASH_COMMANDS)
        choice = await self.push_screen_wait(SlashPickScreen(hits[:40]))
        if choice:
            inp = self.query_one("#prompt", Input)
            inp.value = choice + " "
            inp.focus()

    def _reply_perm(self, decision: str) -> None:
        if not self._perm_pending:
            ok = self.runtime.answer_permission_latest(decision)
        else:
            rid = str(self._perm_pending.get("request_id") or "")
            ok = (
                self.runtime.answer_permission(rid, decision)
                if rid
                else self.runtime.answer_permission_latest(decision)
            )
            self._perm_pending = None
        self._set_perm_bar(False)
        chat = self.query_one("#chat", RichLog)
        chat.write(Text.from_markup(f"[yellow]permission → {decision}[/] ok={ok}"))

    def action_perm_allow(self) -> None:
        if self._perm_pending or (
            self.runtime.permission_broker and self.runtime.permission_broker.pending
        ):
            self._reply_perm("allow")

    def action_perm_deny(self) -> None:
        if self._perm_pending or (
            self.runtime.permission_broker and self.runtime.permission_broker.pending
        ):
            self._reply_perm("deny")

    def action_perm_always(self) -> None:
        if self._perm_pending or (
            self.runtime.permission_broker and self.runtime.permission_broker.pending
        ):
            self._reply_perm("always")

    def action_esc_undo(self) -> None:
        if self._esc_armed:
            self._esc_armed = False
            asyncio.create_task(self._open_rewind())
        else:
            self._esc_armed = True
            self.set_timer(0.8, self._clear_esc)

    def _clear_esc(self) -> None:
        self._esc_armed = False

    def action_open_rewind(self) -> None:
        asyncio.create_task(self._open_rewind())

    async def _open_rewind(self) -> None:
        chat = self.query_one("#chat", RichLog)
        if not self.runtime.file_history or not self.runtime.session_id:
            chat.write(Text.from_markup("[yellow]no file history[/]"))
            return
        pts = await self.runtime.file_history.list_points(self.runtime.session_id, limit=40)
        if not pts:
            chat.write(Text.from_markup("[yellow]no checkpoints yet — edit a file or /checkpoint[/]"))
            return
        rows = [p.to_dict() for p in pts]
        choice = await self.push_screen_wait(RewindScreen(rows))
        if not choice:
            return
        point_id = choice.get("point_id")
        scope = str(choice.get("scope") or "code")
        preview = bool(choice.get("preview"))
        only_paths: list[str] | None = None

        if choice.get("file_pick") and scope in ("code", "both") and self.runtime.file_history:
            dry = await self.runtime.file_history.rewind(
                self.runtime.session_id,
                point_id,
                scope="code",
                dry_run=True,
            )
            flist = [
                f
                for f in (dry.get("diff_files") or dry.get("files") or [])
                if f.get("status") in ("restore", "delete", "create")
            ]
            if flist:
                picked = await self.push_screen_wait(FilePickScreen(flist))
                if picked is None:
                    return
                if not picked:
                    chat.write(Text.from_markup("[yellow]no files selected[/]"))
                    return
                only_paths = picked

        msg = await self.runtime.rewind_to(
            point_id,
            scope=scope,
            dry_run=preview,
            only_paths=only_paths,
        )
        chat.write(
            Text.from_markup(f"[yellow]{escape(msg.split(chr(10)+chr(10))[0] if msg else '')}[/]")
        )
        self._render_rewind_side(focus_only=False)
        # After preview, open hunk workbench for interactive selective apply
        if preview:
            chat.write(Text.from_markup("[dim]preview 完成 → 打开 Hunk 勾选工作台…[/]"))
            await self._open_hunks()

    def action_open_hunks(self) -> None:
        asyncio.create_task(self._open_hunks())

    async def _open_hunks(self) -> None:
        chat = self.query_one("#chat", RichLog)
        from takton_code.agent.hunks import parse_unified_hunks

        res = getattr(self.runtime, "_last_rewind", None) or {}
        udiffs = res.get("unified_diffs") or []
        if not udiffs:
            if self.runtime.file_history and self.runtime.session_id:
                pts = await self.runtime.file_history.list_points(self.runtime.session_id, limit=1)
                if pts:
                    dry = await self.runtime.file_history.rewind(
                        self.runtime.session_id, pts[0].id, dry_run=True
                    )
                    self.runtime._last_rewind = dry
                    udiffs = dry.get("unified_diffs") or []
                    res = dry
            if not udiffs:
                chat.write(Text.from_markup("[yellow]no diffs — Rewind+Preview 或先改文件[/]"))
                return

        file_payloads: list[dict[str, Any]] = []
        for u in udiffs:
            path = str(u.get("path") or "")
            patch = u.get("patch") or ""
            hunks = parse_unified_hunks(patch)
            if hunks:
                file_payloads.append({"path": path, "patch": patch, "hunks": hunks})
        if not file_payloads:
            chat.write(Text.from_markup("[yellow]no parseable hunks[/]"))
            return

        # start on focused file if any
        focus_path = None
        fi = int(res.get("focus_index") or 0)
        if 0 <= fi < len(udiffs):
            focus_path = str(udiffs[fi].get("path") or "")
        screen = HunkPickScreen(file_payloads)
        if focus_path:
            for i, f in enumerate(file_payloads):
                if f.get("path") == focus_path:
                    screen.file_idx = i
                    break
        result = await self.push_screen_wait(screen)
        if result is None:
            return
        applies = result.get("applies") or []
        if not applies:
            chat.write(Text.from_markup("[yellow]no hunks selected[/]"))
            return
        if not self.runtime.file_history or not self.runtime.session_id:
            return

        logs: list[str] = []
        for ap in applies:
            out = await self.runtime.file_history.apply_hunks(
                self.runtime.session_id,
                ap["path"],
                list(ap.get("indices") or []),
                patch=ap.get("patch") or "",
                push_redo=True,
            )
            if out.get("ok"):
                logs.append(
                    f"✓ {ap['path']} hunks={ap.get('indices')}"
                )
            else:
                logs.append(f"✗ {ap['path']}: {out.get('error')}")
            if out.get("side_summary"):
                self.runtime._last_rewind = {
                    **(self.runtime._last_rewind or {}),
                    "side_summary": out["side_summary"],
                }
        for line in logs:
            chat.write(Text.from_markup(f"[yellow]{escape(line)}[/]"))
        self._render_rewind_side(focus_only=False)

    def _render_rewind_side(self, *, focus_only: bool = False) -> None:
        try:
            side = self.query_one("#side-log", RichLog)
            side.clear()
            last = getattr(self.runtime, "_last_rewind", None) or {}
            if focus_only and last.get("side_summary_focus"):
                summary = last["side_summary_focus"]
            elif focus_only:
                from takton_code.agent.file_history import format_rewind_side_panel

                summary = format_rewind_side_panel(last, focus_only=True)
            else:
                summary = last.get("side_summary") or ""
            for line in summary.splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    side.write(Text.from_markup(f"[green]{escape(line)}[/]"))
                elif line.startswith("-") and not line.startswith("---"):
                    side.write(Text.from_markup(f"[red]{escape(line)}[/]"))
                elif line.startswith("@@"):
                    side.write(Text.from_markup(f"[magenta]{escape(line)}[/]"))
                elif line.startswith("──") or line.startswith("▶"):
                    side.write(Text.from_markup(f"[bold cyan]{escape(line)}[/]"))
                else:
                    side.write(Text.from_markup(f"[cyan]{escape(line)}[/]"))
        except Exception:
            pass

    def action_patch_next(self) -> None:
        body = self.runtime.focus_rewind_patch("next")
        self._render_rewind_side(focus_only=True)
        try:
            self.query_one("#chat", RichLog).write(
                Text.from_markup(f"[dim]patch → {escape(body.splitlines()[2] if body else '')}[/]")
            )
        except Exception:
            pass

    def action_patch_prev(self) -> None:
        body = self.runtime.focus_rewind_patch("prev")
        self._render_rewind_side(focus_only=True)
        try:
            self.query_one("#chat", RichLog).write(
                Text.from_markup(f"[dim]patch ← {escape(body.splitlines()[2] if body else '')}[/]")
            )
        except Exception:
            pass

    def action_unrewind(self) -> None:
        asyncio.create_task(self._do_unrewind())

    async def _do_unrewind(self) -> None:
        msg = await self.runtime.unrewind()
        chat = self.query_one("#chat", RichLog)
        chat.write(
            Text.from_markup(f"[yellow]{escape(msg.splitlines()[0] if msg else 'unrewind')}[/]")
        )
        self._render_rewind_side(focus_only=False)

    async def _do_undo(self) -> None:
        msg = await self.runtime.undo_last_turn()
        self.query_one("#chat", RichLog).write(Text.from_markup(f"[yellow]{escape(msg)}[/]"))

    def action_clear_view(self) -> None:
        self.query_one("#chat", RichLog).clear()

    def action_toggle_side(self) -> None:
        side = self.query_one("#side")
        self._side_visible = not self._side_visible
        side.display = self._side_visible


async def run_tui(
    runtime: AgentRuntime,
    *,
    hub: SessionHub | None = None,
    open_session_cb: Any | None = None,
) -> int:
    runtime.stream = bool(getattr(runtime.settings_agent, "stream", True))
    app = TaktonCodeApp(runtime, hub=hub, open_session_cb=open_session_cb)
    return await app.run_async()
