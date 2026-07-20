"""
工作区文件记忆（Claude Code 风格，无 RAG 默认主路径）

布局（在 workspace / 项目根下查找）：
  memory.md              主索引 — 会话开始加载，上限 200 行或 25KB
  memory_temp.md         临时草稿 — 上限 100 行或 12KB
  memory/YYYY-MM-DD.md   近期短记忆 — 默认最近 3 天，各 8KB
  memory-YYYY-MM-DD.md   同上（扁平命名兼容）
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Claude Code MEMORY.md：200 行或 25KB 先到先得
_INDEX_MAX_LINES = 200
_INDEX_MAX_BYTES = 25 * 1024
_TEMP_MAX_LINES = 100
_TEMP_MAX_BYTES = 12 * 1024
_DAILY_MAX_BYTES = 8 * 1024
_RECENT_DAYS = 3

_DATE_RE = re.compile(r"^(?:memory[_-])?(\d{4}-\d{2}-\d{2})\.md$", re.I)


def _candidate_roots(extra_roots: list[str | Path] | None = None) -> list[Path]:
    roots: list[Path] = []
    for r in extra_roots or []:
        roots.append(Path(r))
    for env_key in (
        "TAKTON_FILE_BROWSER_ROOT",
        "TAKTON_WORKSPACE_DIR",
        "TAKTON_PROJECT_ROOT",
    ):
        v = (os.environ.get(env_key) or "").strip()
        if v:
            roots.append(Path(v))
    # userData workspace (desktop)
    appdata = os.environ.get("APPDATA") or os.environ.get("HOME") or ""
    if appdata:
        roots.append(Path(appdata) / "takton" / "data" / "workspace")
        roots.append(Path(appdata) / "Takton" / "data" / "workspace")
    roots.append(Path.cwd())
    try:
        roots.append(Path(__file__).resolve().parents[2])
    except Exception:
        pass
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        try:
            key = str(r.resolve()) if r.exists() else str(r)
        except Exception:
            key = str(r)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _cap_text(text: str, max_lines: int, max_bytes: int) -> str:
    raw = text.replace("\r\n", "\n")
    # bytes cap (utf-8)
    b = raw.encode("utf-8")
    if len(b) > max_bytes:
        raw = b[:max_bytes].decode("utf-8", errors="ignore")
        raw = raw + "\n\n…[truncated by size]"
    lines = raw.split("\n")
    if len(lines) > max_lines:
        raw = "\n".join(lines[:max_lines]) + "\n\n…[truncated by lines]"
    return raw.strip()


def _read(path: Path, max_lines: int, max_bytes: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.debug("read memory file failed %s: %s", path, e)
        return ""
    return _cap_text(text, max_lines, max_bytes)


def find_memory_md(extra_roots: list[str | Path] | None = None) -> Path | None:
    for root in _candidate_roots(extra_roots):
        for name in ("memory.md", "MEMORY.md"):
            p = root / name
            if p.is_file():
                return p
            for sub in ("memory", "docs", ".takton", "config"):
                p2 = root / sub / name
                if p2.is_file():
                    return p2
    return None


def load_memory_md(
    extra_roots: list[str | Path] | None = None,
    max_chars: int = _INDEX_MAX_BYTES,
) -> tuple[str, str | None]:
    """兼容旧 API：仅主索引。"""
    path = find_memory_md(extra_roots)
    if path is None:
        return "", None
    text = _read(path, _INDEX_MAX_LINES, min(max_chars, _INDEX_MAX_BYTES))
    return text, str(path)


def _iter_dated_files(root: Path, days: int) -> list[Path]:
    found: list[Path] = []
    today = date.today()
    want = {(today - timedelta(days=i)).isoformat() for i in range(days)}
    # flat: memory-YYYY-MM-DD.md / memory_YYYY-MM-DD.md
    try:
        for p in root.iterdir():
            if not p.is_file():
                continue
            m = _DATE_RE.match(p.name)
            if m and m.group(1) in want:
                found.append(p)
    except Exception:
        pass
    # dir memory/YYYY-MM-DD.md
    mem_dir = root / "memory"
    if mem_dir.is_dir():
        try:
            for p in mem_dir.iterdir():
                if not p.is_file():
                    continue
                stem = p.stem  # YYYY-MM-DD
                if stem in want and p.suffix.lower() == ".md":
                    found.append(p)
        except Exception:
            pass
    # newest first
    found.sort(key=lambda p: p.name, reverse=True)
    return found


def load_workspace_memory_bundle(
    extra_roots: list[str | Path] | None = None,
    recent_days: int = _RECENT_DAYS,
) -> tuple[str, dict[str, str | None]]:
    """
    组装注入 system 的记忆块。

    Returns:
        (markdown_block, meta paths)
    """
    meta: dict[str, str | None] = {
        "memory_md": None,
        "memory_temp": None,
        "dated": None,
    }
    sections: list[str] = []
    roots = _candidate_roots(extra_roots)

    # 1) memory.md index
    index_path = find_memory_md(extra_roots)
    if index_path:
        body = _read(index_path, _INDEX_MAX_LINES, _INDEX_MAX_BYTES)
        if body:
            sections.append(f"### memory.md（索引）\n{body}")
            meta["memory_md"] = str(index_path)

    # 2) memory_temp.md
    temp_path: Path | None = None
    for root in roots:
        for name in ("memory_temp.md", "MEMORY_TEMP.md"):
            p = root / name
            if p.is_file():
                temp_path = p
                break
            p2 = root / "memory" / name
            if p2.is_file():
                temp_path = p2
                break
        if temp_path:
            break
    if temp_path:
        body = _read(temp_path, _TEMP_MAX_LINES, _TEMP_MAX_BYTES)
        if body:
            sections.append(f"### memory_temp.md（临时）\n{body}")
            meta["memory_temp"] = str(temp_path)

    # 3) dated short memories
    dated_parts: list[str] = []
    dated_paths: list[str] = []
    seen_dates: set[str] = set()
    for root in roots:
        for p in _iter_dated_files(root, recent_days):
            # de-dupe by date key
            m = _DATE_RE.match(p.name)
            dkey = m.group(1) if m else p.stem
            if dkey in seen_dates:
                continue
            seen_dates.add(dkey)
            body = _read(p, 80, _DAILY_MAX_BYTES)
            if body:
                dated_parts.append(f"#### {dkey}\n{body}")
                dated_paths.append(str(p))
    if dated_parts:
        sections.append("### 近期短记忆（按日）\n" + "\n\n".join(dated_parts))
        meta["dated"] = ";".join(dated_paths)

    if not sections:
        return "", meta

    header = (
        "## WORKSPACE MEMORY（本地文件记忆 · 无向量 RAG 时的默认知识层）\n"
        "以下为磁盘记忆，按需可信；细节文件未展开时可用文件工具读取。\n"
    )
    return header + "\n\n".join(sections), meta


# ── 持久人设 / 规范（Hermes 风格）────────────────────────────────

_PERSONA_FILES: tuple[tuple[str, tuple[str, ...], int, int], ...] = (
    # key, filenames, max_lines, max_bytes
    ("identity", ("IDENTITY.md", "identity.md"), 400, 48 * 1024),
    ("soul", ("SOUL.md", "soul.md"), 300, 32 * 1024),
    ("claude", ("CLAUDE.md", "claude.md"), 300, 32 * 1024),
    ("agents", ("AGENTS.md", "agents.md"), 300, 32 * 1024),
)


def _find_named_md(names: tuple[str, ...], extra_roots: list[str | Path] | None = None) -> Path | None:
    for root in _candidate_roots(extra_roots):
        for name in names:
            p = root / name
            if p.is_file():
                return p
            for sub in ("config", ".takton", "docs", "persona", "profile"):
                p2 = root / sub / name
                if p2.is_file():
                    return p2
    return None


def load_workspace_persona_bundle(
    extra_roots: list[str | Path] | None = None,
) -> tuple[str | None, str | None, dict[str, str | None]]:
    """从 workspace 加载持久人设文件。

    Returns:
        (identity_text, context_files_markdown, meta_paths)

    - IDENTITY.md → identity（覆盖默认助手身份）
    - SOUL.md / CLAUDE.md / AGENTS.md → 拼入 context 层（决策/输出/子代理规范）
    """
    meta: dict[str, str | None] = {
        "identity": None,
        "soul": None,
        "claude": None,
        "agents": None,
    }
    identity_text: str | None = None
    context_sections: list[str] = []

    for key, names, max_lines, max_bytes in _PERSONA_FILES:
        path = _find_named_md(names, extra_roots)
        if path is None:
            continue
        body = _read(path, max_lines, max_bytes)
        if not body:
            continue
        meta[key] = str(path)
        if key == "identity":
            identity_text = body
        else:
            label = path.name
            context_sections.append(f"### {label}\n{body}")

    if not identity_text and not context_sections:
        return None, None, meta

    context_block: str | None = None
    if context_sections:
        context_block = (
            "## WORKSPACE PERSONA（磁盘持久规范 · 每次会话自动加载）\n"
            "以下文件来自 workspace，与 IDENTITY 一起构成长期行为约束；"
            "优先级高于闲聊习惯，低于用户本轮明确指令。\n\n"
            + "\n\n".join(context_sections)
        )
    return identity_text, context_block, meta
