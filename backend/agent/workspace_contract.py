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

    # 数据地图：避免 Agent 在错误目录搜目标/记忆
    try:
        from backend.tools.permissions import (
            host_data_roots,
            resolve_agent_workspace_root,
        )

        ws = resolve_agent_workspace_root()
        host_roots = host_data_roots()[:6]
        map_lines = [
            "### DATA MAP（路径事实 · 必读）",
            f"- **workspace_root（file_read/grep/command 默认边界）**: `{ws}`",
            f"- **宿主数据根（已放行）**: " + (", ".join(f"`{r}`" for r in host_roots) if host_roots else "（无）"),
            f"- **沙箱内宿主数据镜像**: `{ws}/.computers/<agent>/home/.tevarn` → 指向宿主 `~/.tevarn`",
            "- **经营目标 O-KR**: 不在文件里，用工具 **`okr_goal`**（list/update/create），不要 grep 源码",
            "- **会话 Todo 规划卡**: `manage_goal`（与目标页无关）",
            "- Windows：`echo`/`dir` 是 cmd 内置；沙箱里优先 `cmd /c echo ok`、`cmd /c dir`",
        ]
        sections.insert(0, "\n".join(map_lines))
        meta["data_map"] = {"workspace_root": ws, "host_roots": host_roots}
    except Exception as e:
        logger.debug("data map inject skip: %s", e)

    if not sections:
        return "", meta

    block = (
        "## WORKSPACE CONTRACT（会话启动契约 · 自动注入）\n"
        "下列文件来自项目/workspace 根、桌面 userData workspace "
        "（`%APPDATA%/tevarn/data/workspace`）或 `.computers/*/home`。"
        "缺失会标明 [missing]；大文件会截断并带 [truncated]。"
        "用户本轮明确指令优先于契约。"
        "Windows 默认 cmd：串联用 `&`，列目录用 `dir`。\n\n"
        + "\n\n".join(sections)
    )
    return block, meta


__all__ = ["load_workspace_contract"]
