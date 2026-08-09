"""Client-side UX helpers: vim mode state + command palette actions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PaletteAction:
    id: str
    label: str
    keys: str = ""
    category: str = "general"


# Stable catalog for palette + help
PALETTE_ACTIONS: list[PaletteAction] = [
    PaletteAction("mode", "切换 Mode（点选）", "Tab / Mode", "session"),
    PaletteAction("rewind", "Rewind 检查点", "Ctrl+R / EscEsc", "history"),
    PaletteAction("hunks", "Hunk 工作台", "h", "history"),
    PaletteAction("unrewind", "Unrewind / redo", "Ctrl+Shift+Z", "history"),
    PaletteAction("diff", "显示 Diff", "Ctrl+O", "view"),
    PaletteAction("queue", "显示 Queue", "Ctrl+;", "view"),
    PaletteAction("todos", "显示 Todos", "Todos", "view"),
    PaletteAction("sessions", "会话列表", "Ctrl+\\", "session"),
    PaletteAction("slash", "Slash 命令", "Ctrl+P", "input"),
    PaletteAction("side", "切换侧栏", "F2", "view"),
    PaletteAction("stop", "停止当前 turn", "Ctrl+C", "session"),
    PaletteAction("clear", "清空聊天视图", "Ctrl+L", "view"),
    PaletteAction("compact", "压缩上下文 /compact", "", "session"),
    PaletteAction("export_md", "导出会话 Markdown", "", "session"),
    PaletteAction("export_jsonl", "导出会话 JSONL", "", "session"),
    PaletteAction("fork", "Fork 会话", "", "session"),
    PaletteAction("yank", "复制最后一条助手回复", "yy", "edit"),
    PaletteAction("insert", "进入插入模式（输入框）", "i / a", "edit"),
    PaletteAction("search", "搜索日志 /pattern", "/ n N", "edit"),
    PaletteAction("vim_help", "Vim 键位帮助", "?", "help"),
]


def filter_palette(query: str) -> list[PaletteAction]:
    q = (query or "").strip().lower()
    if not q:
        return list(PALETTE_ACTIONS)
    out = []
    for a in PALETTE_ACTIONS:
        blob = f"{a.id} {a.label} {a.keys} {a.category}".lower()
        if q in blob:
            out.append(a)
    return out


@dataclass
class SearchHit:
    line_index: int
    line: str
    start: int
    end: int


def find_hits(lines: list[str], query: str, *, ignore_case: bool = True) -> list[SearchHit]:
    """Find all case-insensitive substring hits in plain log lines."""
    q = (query or "").strip()
    if not q:
        return []
    flags = re.IGNORECASE if ignore_case else 0
    try:
        rx = re.compile(re.escape(q), flags)
    except re.error:
        return []
    hits: list[SearchHit] = []
    for i, line in enumerate(lines):
        plain = _strip_markup(line)
        for m in rx.finditer(plain):
            hits.append(SearchHit(i, plain, m.start(), m.end()))
    return hits


def highlight_line(line: str, query: str, *, current: bool = False) -> str:
    """Return Rich markup with query spans highlighted."""
    q = (query or "").strip()
    plain = _strip_markup(line)
    if not q:
        return plain
    try:
        rx = re.compile(re.escape(q), re.IGNORECASE)
    except re.error:
        return plain
    parts: list[str] = []
    last = 0
    style = "bold reverse yellow" if current else "bold yellow on #3d2e00"
    for m in rx.finditer(plain):
        if m.start() > last:
            parts.append(_escape_markup(plain[last : m.start()]))
        parts.append(f"[{style}]{_escape_markup(m.group(0))}[/]")
        last = m.end()
    if last < len(plain):
        parts.append(_escape_markup(plain[last:]))
    return "".join(parts) if parts else _escape_markup(plain)


def _strip_markup(s: str) -> str:
    # crude strip of rich tags
    return re.sub(r"\[/?[^\]]*\]", "", s or "")


def _escape_markup(s: str) -> str:
    return (s or "").replace("[", "\\[")


@dataclass
class VimState:
    """INSERT = typing; NORMAL = navigate; SEARCH = /query entry handled externally."""

    mode: str = "insert"  # insert | normal | search
    pending: str = ""  # multi-key (g, y, …)
    focus: str = "chat"  # chat | side
    count_str: str = ""  # digit accumulator e.g. "10"
    search_query: str = ""
    search_hits: list[SearchHit] = field(default_factory=list)
    search_idx: int = -1

    def enter_normal(self) -> None:
        self.mode = "normal"
        self.pending = ""
        self.count_str = ""

    def enter_insert(self) -> None:
        self.mode = "insert"
        self.pending = ""
        self.count_str = ""

    def enter_search(self) -> None:
        self.mode = "search"
        self.pending = ""
        self.count_str = ""

    def feed_digit(self, d: str) -> None:
        if d == "0" and not self.count_str:
            # bare 0 is motion, not count
            return
        if d.isdigit():
            # cap insane counts
            if len(self.count_str) < 4:
                self.count_str += d

    def take_count(self, default: int = 1) -> int:
        if not self.count_str:
            n = default
        else:
            try:
                n = max(1, int(self.count_str))
            except ValueError:
                n = default
        self.count_str = ""
        return min(n, 9999)

    def clear_pending(self) -> None:
        self.pending = ""
        self.count_str = ""

    def set_hits(self, hits: list[SearchHit]) -> None:
        self.search_hits = hits
        self.search_idx = 0 if hits else -1

    def next_hit(self) -> SearchHit | None:
        if not self.search_hits:
            return None
        self.search_idx = (self.search_idx + 1) % len(self.search_hits)
        return self.search_hits[self.search_idx]

    def prev_hit(self) -> SearchHit | None:
        if not self.search_hits:
            return None
        self.search_idx = (self.search_idx - 1) % len(self.search_hits)
        return self.search_hits[self.search_idx]

    def current_hit(self) -> SearchHit | None:
        if self.search_idx < 0 or self.search_idx >= len(self.search_hits):
            return None
        return self.search_hits[self.search_idx]

    def label(self) -> str:
        if self.mode == "search":
            return f"SEARCH /{self.search_query}"
        if self.mode == "normal":
            bits = ["NORMAL"]
            if self.count_str:
                bits.append(self.count_str)
            if self.pending:
                bits.append(f"+{self.pending}")
            if self.search_query and self.search_hits:
                bits.append(f"/{self.search_query} {self.search_idx + 1}/{len(self.search_hits)}")
            elif self.search_query:
                bits.append(f"/{self.search_query} 0")
            bits.append(self.focus)
            return " · ".join(bits)
        return "INSERT"
