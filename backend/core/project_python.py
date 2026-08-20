"""解析 Tevarn 项目 Python 解释器，避免 PATH 上 hermes/其它 venv 污染编制 command。

优先级：
1. 环境变量 TEVARN_PYTHON
2. 当前进程 sys.executable（backend 已在项目 venv 内时）
3. 仓库根 .venv/Scripts/python.exe 或 .venv/bin/python
4. sys.executable 兜底
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

logger = __import__("logging").getLogger(__name__)

# 匹配命令开头或 shell 连接符后的 python / python3 / py -3。
# `python` 忽略大小写；裸 `py` 必须小写。IGNORECASE 会把 heredoc 结束符 PY 误替换。
_PY_TOKEN = re.compile(
    r"(?P<pre>^|[\n;&|]\s*)(?P<py>(?i:python3?)|py(?:\s+-3)?)\b(?=\s|$)",
)


def project_root() -> Path:
    """backend/core/project_python.py → 仓库根。"""
    return Path(__file__).resolve().parents[2]


def resolve_project_python() -> str:
    env = (os.environ.get("TEVARN_PYTHON") or "").strip()
    if env and Path(env).is_file():
        return str(Path(env).resolve())

    exe = Path(sys.executable).resolve()
    # 已在项目 venv 内
    try:
        root = project_root()
        if str(exe).lower().startswith(str(root.resolve()).lower()) and (
            ".venv" in exe.parts or "venv" in exe.parts
        ):
            return str(exe)
    except Exception:
        pass

    root = project_root()
    candidates = [
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
        root / "venv" / "Scripts" / "python.exe",
        root / "venv" / "bin" / "python",
    ]
    for c in candidates:
        if c.is_file():
            return str(c.resolve())

    return str(exe)


def _quote_exe(path: str) -> str:
    if re.search(r'[\s"]', path):
        return '"' + path.replace('"', r"\"") + '"'
    return path


def rewrite_command_python(command: str) -> tuple[str, bool]:
    """把命令中的 python/python3/py -3 换成项目解释器。

    返回 (new_command, changed)。
    """
    if not command or not command.strip():
        return command, False
    py = _quote_exe(resolve_project_python())
    changed = False

    def _sub(m: re.Match[str]) -> str:
        nonlocal changed
        changed = True
        return f"{m.group('pre')}{py}"

    new = _PY_TOKEN.sub(_sub, command)
    if changed:
        logger.info(
            "rewrote python in command → %s …",
            resolve_project_python()[:80],
        )
    return new, changed


__all__ = [
    "project_root",
    "resolve_project_python",
    "rewrite_command_python",
]
