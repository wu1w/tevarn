"""宿主命令解析：在 Electron/精简 PATH 下仍能找到 npx / node / uvx。

问题背景：
- MCP stdio 用 CreateProcess 起子进程，依赖 shutil.which(command)
- 桌面端后端 PATH 可能不含 Program Files\\nodejs 或 Python Scripts
- 裸写 command=\"npx\" / \"uvx\" → 找不到 → fetch/tavily/firecrawl 全挂

本模块：
1. 补齐常见工具目录到 PATH
2. 把 npx/uvx/node 等解析成绝对路径（Windows 优先 .cmd/.exe）
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_WIN = sys.platform == "win32"


def _unique_dirs(dirs: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for d in dirs:
        d = (d or "").strip().strip('"')
        if not d:
            continue
        key = d.lower() if _WIN else d
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def common_tool_dirs() -> list[str]:
    """已知可能含 npx/node/uvx 的目录（存在才加入）。"""
    home = Path.home()
    cands: list[Path] = [
        Path(sys.prefix) / ("Scripts" if _WIN else "bin"),
        Path(sys.executable).resolve().parent,
        Path(sys.executable).resolve().parent / "Scripts",
        # 系统 Node
        Path(r"C:\Program Files\nodejs"),
        Path(r"C:\Program Files (x86)\nodejs"),
        home / "AppData" / "Roaming" / "npm",
        home / "AppData" / "Local" / "Programs" / "nodejs",
        # 常见 uv 用户安装
        home / ".local" / "bin",
        home / "AppData" / "Local" / "uv",
        # Tevarn 便携 Python（含旧目录软迁移）
        Path(r"C:\Users") / os.environ.get("USERNAME", "") / "AppData" / "Local" / "Programs" / "tevarn" / "resources" / "python" / "Scripts",
        home / "AppData" / "Local" / "Programs" / "tevarn" / "resources" / "python" / "Scripts",
        home / ".takton" / "python" / "Scripts",
        home / ".tevarn" / "python" / "Scripts",
    ]
    # 环境变量提示
    for key in ("NODE_HOME", "NODEJS_HOME", "NPM_CONFIG_PREFIX"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            cands.append(Path(raw))
            cands.append(Path(raw) / "bin")

    out: list[str] = []
    for p in cands:
        try:
            if p.is_dir():
                out.append(str(p.resolve()))
        except Exception:
            continue
    return _unique_dirs(out)


def enrich_path(base: str | None = None) -> str:
    """把 common_tool_dirs 前插到 PATH，保留原有条目。"""
    original = base if base is not None else (os.environ.get("PATH") or "")
    parts = common_tool_dirs() + [p for p in original.split(os.pathsep) if p]
    return os.pathsep.join(_unique_dirs(parts))


def _which_in_path(command: str, path: str) -> str | None:
    """在指定 PATH 上查找，不改全局 os.environ（避免并发 which 竞态）。"""
    try:
        found = shutil.which(command, path=path)
        if found:
            return found
        if _WIN:
            for ext in (".cmd", ".bat", ".exe", ".ps1"):
                found = shutil.which(command + ext, path=path)
                if found:
                    return found
        return None
    except TypeError:
        # 极老 Python 无 path= 参数时回退（会短暂改 env）
        old = os.environ.get("PATH")
        try:
            os.environ["PATH"] = path
            found = shutil.which(command)
            if found:
                return found
            if _WIN:
                for ext in (".cmd", ".bat", ".exe", ".ps1"):
                    found = shutil.which(command + ext)
                    if found:
                        return found
            return None
        finally:
            if old is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = old


def resolve_host_command(command: str | None) -> str:
    """将 npx/uvx/node 等解析为绝对路径；解析失败原样返回。"""
    cmd = (command or "").strip().strip('"')
    if not cmd:
        return command or ""
    # 已是绝对路径且存在
    try:
        p = Path(cmd)
        if p.is_file():
            return str(p.resolve())
    except Exception:
        pass

    path = enrich_path()
    found = _which_in_path(cmd, path)
    if found:
        return found

    # 硬编码兜底（Windows 最常见）
    name = cmd.lower().removesuffix(".cmd").removesuffix(".exe").removesuffix(".bat")
    hard: list[Path] = []
    if name in ("npx", "npm", "node"):
        hard.extend(
            [
                Path(r"C:\Program Files\nodejs") / (f"{name}.cmd" if name != "node" else "node.exe"),
                Path(r"C:\Program Files\nodejs") / f"{name}.exe",
                Path(r"C:\Program Files (x86)\nodejs") / (f"{name}.cmd" if name != "node" else "node.exe"),
            ]
        )
    if name in ("uvx", "uv"):
        hard.extend(
            [
                Path(sys.prefix) / "Scripts" / f"{name}.exe",
                Path(sys.executable).resolve().parent / "Scripts" / f"{name}.exe",
                Path.home() / ".local" / "bin" / name,
                Path.home() / ".local" / "bin" / f"{name}.exe",
            ]
        )
    for hp in hard:
        try:
            if hp.is_file():
                return str(hp.resolve())
        except Exception:
            continue

    logger.debug("resolve_host_command: keep bare %r (not found on enriched PATH)", cmd)
    return cmd


def build_process_env(
    extra: dict[str, str] | None = None,
    *,
    inherit_path: bool = True,
) -> dict[str, str]:
    """给子进程用的 env：至少带补全后的 PATH + PATHEXT。"""
    env: dict[str, str] = {}
    # 继承 MCP SDK 同款安全键
    keys = (
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "USERNAME",
        "USERDOMAIN",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "COMSPEC",
        "PATHEXT",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "HOME",
        "LANG",
        "LC_ALL",
        "TERM",
    )
    for k in keys:
        v = os.environ.get(k)
        if v:
            env[k] = v
    base_path = os.environ.get("PATH", "") if inherit_path else ""
    env["PATH"] = enrich_path(base_path)
    if _WIN and "PATHEXT" not in env:
        env["PATHEXT"] = ".COM;.EXE;.BAT;.CMD;.VBS;.JS;.WS;.MSC"
    # 代理：子进程（npx 拉包 / uvx）也需要
    try:
        from backend.core.outbound_http import resolve_proxy_url

        proxy = resolve_proxy_url()
        if proxy:
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
                env[k] = proxy
    except Exception:
        pass
    if extra:
        for k, v in extra.items():
            if v is None:
                continue
            env[str(k)] = str(v)
        # 用户 env 若带 PATH 则再补全
        if "PATH" in (extra or {}):
            env["PATH"] = enrich_path(str(extra.get("PATH") or ""))
    return env
