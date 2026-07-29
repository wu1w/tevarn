"""安全子进程启动：优先 list/exec，避免无必要的 shell 字符串注入面。

设计：
- 简单命令（无管道/重定向/逻辑符）→ create_subprocess_exec(argv)
- 需要 shell 功能时 → create_subprocess_shell，但先做基础注入字符检查
- 禁止 NUL / 控制字符
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import sys
from typing import Any

logger = logging.getLogger(__name__)

# 需要 shell 解析的元字符（跨平台保守集）
# 注意：不把裸 () 算进去 —— `python -c "print(1)"` 应走 exec；
# 子 shell $() / `` 已单独覆盖。
_SHELL_META = re.compile(r"[|;&`$<>\n\r]|&&|\|\||>>|<<|\$\(")
# Windows cmd 注入常见符（在 shell 模式下额外拦截可疑组合）
_WIN_INJECT = re.compile(
    r"(&\s*&)|(\|\s*\|)|(%\w+%)|(\^)|(`)|(\$\()|(;\s*(rm|del|format|shutdown)\b)",
    re.I,
)


def needs_shell(command: str) -> bool:
    """是否依赖 shell 语法（管道、重定向、&& 等）。"""
    if not command:
        return False
    if _SHELL_META.search(command):
        return True
    # Windows 内置（无 .exe 的 cmd 内建）在 shell=False 下可能找不到
    if sys.platform == "win32":
        head = command.strip().split(None, 1)[0].lower() if command.strip() else ""
        head = head.strip("\"'")
        builtins = {
            "dir",
            "cd",
            "copy",
            "move",
            "type",
            "echo",
            "set",
            "cls",
            "md",
            "mkdir",
            "rd",
            "rmdir",
            "del",
            "ren",
            "rename",
            "start",
            "call",
            "if",
            "for",
        }
        if head in builtins:
            return True
    return False


def split_argv(command: str) -> list[str]:
    """将命令拆成 argv；Windows 用 posix=False，并剥掉 shlex 留下的包围引号。"""
    if sys.platform == "win32":
        parts = shlex.split(command, posix=False)
        cleaned: list[str] = []
        for p in parts:
            if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'":
                cleaned.append(p[1:-1])
            else:
                cleaned.append(p)
        return cleaned
    return shlex.split(command, posix=True)


def validate_command_string(command: str) -> str | None:
    """返回错误信息；None 表示通过。"""
    if not command or not str(command).strip():
        return "empty command"
    if "\x00" in command:
        return "NUL bytes are not allowed"
    # 其它 C0 控制符（保留 tab/newline 给 heredoc 类 shell 命令）
    for ch in command:
        o = ord(ch)
        if o < 32 and ch not in ("\t", "\n", "\r"):
            return f"control character U+{o:04X} is not allowed"
    return None


def validate_app_name(app_name: str) -> str | None:
    """桌面 open_app：禁止注入字符。"""
    name = (app_name or "").strip()
    if not name:
        return "empty app name"
    if len(name) > 260:
        return "app name too long"
    if re.search(r"[;&|<>`$%\n\r]", name):
        return "app name contains shell metacharacters"
    if ".." in name:
        return "app name path traversal not allowed"
    return None


async def create_process(
    command: str,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
) -> asyncio.subprocess.Process:
    """创建子进程：能 exec 则 exec，否则受限 shell。"""
    err = validate_command_string(command)
    if err:
        raise ValueError(err)

    use_shell = needs_shell(command)
    if use_shell and sys.platform == "win32" and _WIN_INJECT.search(command):
        # 高危模式：shell 元字符 + 注入形；仍允许管道类合法命令，
        # 但拒绝 %ENV% / 双重 && 嵌套等常见 cmd 注入形态。
        # 若命令同时含管道与 %VAR%，走 shell 但由上层 danger policy 再拦。
        if re.search(r"%\w+%", command) or re.search(r"`", command):
            raise ValueError(
                "refusing shell command with Windows env-expansion/backtick injection risk"
            )

    if not use_shell:
        try:
            argv = split_argv(command)
        except ValueError as e:
            raise ValueError(f"cannot parse command: {e}") from e
        if not argv:
            raise ValueError("empty argv")
        logger.debug("safe_subprocess exec argv0=%s", argv[0][:80])
        return await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd or None,
            env=env,
            stdout=stdout,
            stderr=stderr,
        )

    logger.debug("safe_subprocess shell cmd=%s", command[:120])
    return await asyncio.create_subprocess_shell(
        command,
        cwd=cwd or None,
        env=env,
        stdout=stdout,
        stderr=stderr,
    )


async def run_capture(
    command: str,
    *,
    cwd: str | None = None,
    timeout: float = 120.0,
    env: dict[str, str] | None = None,
    max_output: int = 50_000,
) -> dict[str, Any]:
    """运行并捕获 stdout/stderr。返回 dict: ok, stdout, stderr, code, mode。"""
    try:
        proc = await create_process(command, cwd=cwd, env=env)
    except ValueError as e:
        return {
            "ok": False,
            "stdout": "",
            "stderr": f"[Security Blocked] {e}",
            "code": -1,
            "mode": "blocked",
        }

    mode = "shell" if needs_shell(command) else "exec"
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=max(1.0, float(timeout))
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return {
            "ok": False,
            "stdout": "",
            "stderr": f"command exceeded {timeout}s and was terminated",
            "code": 124,
            "mode": mode,
        }

    out = (stdout_b or b"").decode("utf-8", errors="replace")
    err = (stderr_b or b"").decode("utf-8", errors="replace")
    if len(out) > max_output:
        out = out[:max_output] + f"\n...[stdout truncated {len(stdout_b or b'')} bytes]"
    if len(err) > max(max_output // 2, 1000):
        err = err[: max_output // 2] + f"\n...[stderr truncated {len(stderr_b or b'')} bytes]"
    code = proc.returncode if proc.returncode is not None else -1
    return {
        "ok": code == 0,
        "stdout": out,
        "stderr": err,
        "code": code,
        "mode": mode,
    }


def launch_app_windows(app_name: str) -> None:
    """Windows 安全启动应用：不用 shell=True 拼接。"""
    import subprocess

    err = validate_app_name(app_name)
    if err:
        raise ValueError(err)
    name = app_name.strip()
    # 优先：可执行路径 / 已注册命令，list 形式
    if os.path.isfile(name) or name.lower().endswith((".exe", ".bat", ".cmd", ".lnk")):
        subprocess.Popen([name], shell=False, close_fds=True)  # noqa: S603
        return
    # 使用 cmd /c start 的 list 形式，空标题避免 start 把第一参数当窗口标题
    # start "" app  — app 作为独立参数，不经 f-string 拼进 shell 行
    subprocess.Popen(  # noqa: S603
        ["cmd.exe", "/c", "start", "", name],
        shell=False,
        close_fds=True,
    )
