"""Workspace 契约文件注入（对齐 OpenClaw bootstrap 风格）。

始终报告 AGENTS.md / SOUL.md / USER.md / TOOLS.md：
- 存在且非空 → 截断后注入
- 缺失 → [missing] 标记（不静默忽略）
- 过大 → [truncated] 标记
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.agent.file_context import _candidate_roots, _cap_text, _find_named_md

logger = logging.getLogger(__name__)

# name, filenames, max_lines, max_bytes
_CONTRACT_FILES: tuple[tuple[str, tuple[str, ...], int, int], ...] = (
    ("AGENTS.md", ("AGENTS.md", "agents.md"), 300, 32 * 1024),
    ("SOUL.md", ("SOUL.md", "soul.md"), 300, 32 * 1024),
    ("USER.md", ("USER.md", "user.md"), 200, 24 * 1024),
    ("TOOLS.md", ("TOOLS.md", "tools.md"), 200, 24 * 1024),
)


def load_workspace_contract(
    extra_roots: list[str | Path] | None = None,
    *,
    include_missing_markers: bool = True,
    only_extra_roots: bool = False,
) -> tuple[str, dict[str, Any]]:
    """构建契约上下文块。

    only_extra_roots=True 时仅在 extra_roots 内查找（测试/隔离用）。

    Returns:
        (markdown_block, meta)
    """
    if only_extra_roots and extra_roots:
        roots = [Path(r) for r in extra_roots]
    else:
        roots = _candidate_roots(extra_roots)
    meta: dict[str, Any] = {"files": {}, "roots_tried": [str(r) for r in roots[:6]]}
    sections: list[str] = []

    def _find_in_roots(names: tuple[str, ...]) -> Path | None:
        if only_extra_roots and extra_roots:
            for root in roots:
                for name in names:
                    p = Path(root) / name
                    if p.is_file():
                        return p
            return None
        return _find_named_md(names, extra_roots)

    for label, names, max_lines, max_bytes in _CONTRACT_FILES:
        path = _find_in_roots(names)
        if path is None:
            meta["files"][label] = {"status": "missing", "path": None}
            if include_missing_markers:
                sections.append(f"### {label}\n[missing: {label} — 未在 workspace/项目根找到]")
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            meta["files"][label] = {"status": "error", "path": str(path), "error": str(e)[:120]}
            sections.append(f"### {label}\n[error reading {path.name}: {e}]")
            continue

        if not (raw or "").strip():
            meta["files"][label] = {"status": "empty", "path": str(path)}
            # 空文件跳过正文，避免占位噪声
            continue

        body = _cap_text(raw, max_lines, max_bytes)
        truncated = body != raw.replace("\r\n", "\n").strip() and (
            "…[truncated" in body or len(raw) > max_bytes
        )
        meta["files"][label] = {
            "status": "truncated" if truncated or "…[truncated" in body else "ok",
            "path": str(path),
            "chars": len(body),
        }
        note = " *(truncated)*" if "…[truncated" in body else ""
        sections.append(f"### {label}{note}\nPath: `{path}`\n\n{body}")

    if not sections:
        return "", meta

    block = (
        "## WORKSPACE CONTRACT（会话启动契约 · 自动注入）\n"
        "下列文件来自项目/workspace 根。缺失会标明 [missing]；"
        "大文件会截断并带 [truncated]。用户本轮明确指令优先于契约。\n\n"
        + "\n\n".join(sections)
    )
    return block, meta


__all__ = ["load_workspace_contract"]
