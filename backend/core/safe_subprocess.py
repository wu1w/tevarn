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
# NOTE: bare %VAR% is NOT here — common vars are expanded first; only
# unknown %FOO% remain suspicious (see expand_safe_windows_env / create_process).
_WIN_INJECT = re.compile(
    r"(&\s*&)|(\|\s*\|)|(\^)|(`)|(\$\()|(;\s*(rm|del|format|shutdown)\b)",
    re.I,
)

# Safe cmd env vars agents commonly use for scoop/cargo paths.
_SAFE_WIN_ENV = frozenset(
    {
        "USERPROFILE",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "USERNAME",
        "USERDOMAIN",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "PATH",
        "PATHEXT",
        "CD",
        "ERRORLEVEL",
        "COMPUTERNAME",
        "LOCALAPPDATA",
        "APPDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMDATA",
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "COMSPEC",
    }
)
_WIN_ENV_TOKEN_RE = re.compile(r"%([^%]+)%")


def expand_safe_windows_env(command: str, env: dict[str, str] | None = None) -> str:
    """Expand allowlisted ``%VAR%`` tokens so cargo/scoop paths work under cmd.

    Rejects remaining ``%UNKNOWN%`` later as injection risk. Without this,
    legitimate agent commands like
    ``set RUSTC=%USERPROFILE%\\scoop\\apps\\rust\\...`` were blocked with
    ``refusing shell command with Windows env-expansion``.
    """
    if not command or "%" not in command or sys.platform != "win32":
        return command
    src = env if env is not None else os.environ

    def _repl(m: re.Match[str]) -> str:
        name = (m.group(1) or "").strip()
        key = name.upper()
        # normalize ProgramFiles(x86) style
        if key not in _SAFE_WIN_ENV and name.upper() not in _SAFE_WIN_ENV:
            return m.group(0)  # leave unknown for later refuse
        # case-insensitive lookup
        val = src.get(name)
        if val is None:
            for k, v in src.items():
                if str(k).upper() == key:
                    val = v
                    break
        if val is None or val == "":
            return m.group(0)
        return str(val)

    return _WIN_ENV_TOKEN_RE.sub(_repl, command)


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


_CMD_WRAPPER_RE = re.compile(
    r"^(?:cmd(?:\.exe)?)\s+(?:/d\s+)?/c\s+(?P<body>.*)$",
    re.I | re.S,
)
_PS_CMDLET_RE = re.compile(
    r"^(Get-ChildItem|Get-Content|Get-Item|Select-Object|Where-Object|"
    r"ForEach-Object|Set-Location|Test-Path|Join-Path|Resolve-Path|"
    r"Write-Output|Write-Host|\$[A-Za-z_])",
    re.I,
)


def _unwrap_one_quoted(s: str) -> str:
    t = (s or "").strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        return t[1:-1]
    return t


def normalize_windows_job_command(command: str) -> str:
    """Normalize model commands for JobBackend which always runs:

        cmd.exe /d /c <command>

    Models often pass ``cmd /c "..."`` already → double wrap produces:
    ``'\"rustc --version & ...\"' 不是内部或外部命令``.

    Also rewrite bare PowerShell cmdlets (Get-ChildItem …) to powershell.exe.
    """
    c = (command or "").strip()
    if not c:
        return c
    # Strip one or two redundant cmd /c wrappers
    for _ in range(2):
        m = _CMD_WRAPPER_RE.match(c)
        if not m:
            break
        body = (m.group("body") or "").strip()
        # cmd /c "inner with &"  → inner with &
        c = _unwrap_one_quoted(body)
    c = c.strip()
    # Bare PowerShell → encoded command (avoids quote hell under cmd /c)
    if _PS_CMDLET_RE.match(c) and not re.match(r"^(powershell|pwsh)(\.exe)?\b", c, re.I):
        try:
            import base64

            # -EncodedCommand expects UTF-16LE
            b64 = base64.b64encode(c.encode("utf-16-le")).decode("ascii")
            return f"powershell.exe -NoProfile -NonInteractive -EncodedCommand {b64}"
        except Exception:
            # fallback: simple -Command with doubled quotes
            esc = c.replace('"', '`"')
            return f'powershell.exe -NoProfile -NonInteractive -Command "{esc}"'
    return c


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


async def kill_process_tree(proc: asyncio.subprocess.Process | None) -> None:
    """Best-effort kill process and children (Windows taskkill / POSIX killpg).

    Outer agent timeouts cancel the asyncio task; without this, children that
    only hit CancelledError (not TimeoutError) keep running and poison later tools.
    """
    if proc is None or proc.returncode is not None:
        return
    pid = getattr(proc, "pid", None)
    try:
        if sys.platform == "win32" and pid:
            # /T = tree; /F = force. Avoids orphaned cmd/python grandchildren.
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(killer.wait(), timeout=5.0)
            except Exception:
                try:
                    killer.kill()
                except Exception:
                    pass
        else:
            # start_new_session=True below → process group id == pid
            if pid and hasattr(os, "killpg"):
                try:
                    os.killpg(pid, 9)  # SIGKILL
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            else:
                try:
                    proc.kill()
                except Exception:
                    pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except Exception:
            pass
    except Exception as e:
        logger.debug("kill_process_tree failed: %s", e)


def _spawn_kwargs() -> dict[str, Any]:
    """Platform kwargs so we can kill the whole process group/tree later."""
    kw: dict[str, Any] = {
        # Never inherit interactive stdin — hangs until outer 180s cancel.
        "stdin": asyncio.subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        # CREATE_NEW_PROCESS_GROUP helps Ctrl-break; taskkill /T still kills tree.
        try:
            import subprocess as _sp

            kw["creationflags"] = getattr(_sp, "CREATE_NEW_PROCESS_GROUP", 0)
        except Exception:
            pass
    else:
        kw["start_new_session"] = True
    return kw


def _windows_selector_loop() -> bool:
    """True when running under WindowsSelectorEventLoop.

    asyncio.create_subprocess_exec/shell raise bare NotImplementedError on
    SelectorEventLoop (Tevarn boot pins this policy). Agent then saw
    ``[Error] NotImplementedError`` / bg ``exit=-1`` with empty stderr.
    """
    if sys.platform != "win32":
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    name = type(loop).__name__
    return "Selector" in name


class _ThreadSubprocess:
    """Process-like wrapper around ``subprocess.Popen`` for SelectorEventLoop.

    Exposes ``communicate`` / ``kill`` / ``returncode`` / ``pid`` so callers of
    ``create_process`` keep working without asyncio subprocess support.
    """

    def __init__(self, popen: Any) -> None:
        self._p = popen
        self.returncode: int | None = None
        self.pid = getattr(popen, "pid", None)

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        def _run() -> tuple[bytes, bytes]:
            return self._p.communicate(input=input)

        out, err = await asyncio.to_thread(_run)
        self.returncode = self._p.returncode
        return out or b"", err or b""

    def kill(self) -> None:
        try:
            self._p.kill()
        except Exception:
            pass

    def terminate(self) -> None:
        try:
            self._p.terminate()
        except Exception:
            pass

    async def wait(self) -> int:
        def _wait() -> int:
            return int(self._p.wait())

        code = await asyncio.to_thread(_wait)
        self.returncode = code
        return code


def _popen_thread_process(
    command: str,
    *,
    use_shell: bool,
    cwd: str | None,
    env: dict[str, str] | None,
) -> _ThreadSubprocess:
    import subprocess as _sp

    creationflags = 0
    if sys.platform == "win32":
        creationflags = int(getattr(_sp, "CREATE_NEW_PROCESS_GROUP", 0) or 0)
    common = dict(
        cwd=cwd or None,
        env=env,
        stdin=_sp.DEVNULL,
        stdout=_sp.PIPE,
        stderr=_sp.PIPE,
        creationflags=creationflags,
    )
    if use_shell:
        # Windows: prefer explicit cmd.exe so quoting matches JobBackend.
        if sys.platform == "win32":
            popen = _sp.Popen(["cmd.exe", "/d", "/c", command], **common)
        else:
            popen = _sp.Popen(command, shell=True, **common)
    else:
        argv = split_argv(command)
        if not argv:
            raise ValueError("empty argv")
        popen = _sp.Popen(argv, shell=False, **common)
    return _ThreadSubprocess(popen)


async def create_process(
    command: str,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
) -> Any:
    """创建子进程：能 exec 则 exec，否则受限 shell。

    On Windows + SelectorEventLoop, falls back to threaded ``subprocess.Popen``
    (asyncio subprocess APIs are NotImplemented there).
    """
    err = validate_command_string(command)
    if err:
        raise ValueError(err)

    # Normalize env to str→str (CreateProcess rejects non-str values).
    run_env: dict[str, str] | None = None
    if env is not None:
        run_env = {str(k): str(v) for k, v in env.items() if k is not None and v is not None}

    # Expand safe %USERPROFILE% etc. before inject checks / spawn.
    if sys.platform == "win32":
        command = expand_safe_windows_env(command, run_env or dict(os.environ))

    use_shell = needs_shell(command)
    if use_shell and sys.platform == "win32" and _WIN_INJECT.search(command):
        # 高危模式：shell 元字符 + 注入形；仍允许管道类合法命令。
        if re.search(r"`", command):
            raise ValueError(
                "refusing shell command with Windows backtick injection risk"
            )
    # After safe expansion, any leftover %VAR% is unknown → refuse
    if sys.platform == "win32" and re.search(r"%\w+%", command):
        raise ValueError(
            "refusing shell command with Windows env-expansion/backtick injection risk "
            f"(unknown %VAR% left after safe expand): {command[:160]}"
        )

    if _windows_selector_loop():
        logger.debug(
            "safe_subprocess: Windows SelectorEventLoop → threaded Popen shell=%s cmd=%s",
            use_shell,
            command[:120],
        )
        return await asyncio.to_thread(
            _popen_thread_process,
            command,
            use_shell=use_shell,
            cwd=cwd,
            env=run_env,
        )

    spawn = _spawn_kwargs()
    try:
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
                env=run_env,
                stdout=stdout,
                stderr=stderr,
                **spawn,
            )

        logger.debug("safe_subprocess shell cmd=%s", command[:120])
        return await asyncio.create_subprocess_shell(
            command,
            cwd=cwd or None,
            env=run_env,
            stdout=stdout,
            stderr=stderr,
            **spawn,
        )
    except NotImplementedError:
        # Belt-and-suspenders: some loop policies still reject subprocess APIs.
        logger.warning(
            "asyncio subprocess NotImplemented; falling back to threaded Popen cmd=%s",
            command[:120],
        )
        return await asyncio.to_thread(
            _popen_thread_process,
            command,
            use_shell=use_shell,
            cwd=cwd,
            env=run_env,
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
        await kill_process_tree(proc)
        return {
            "ok": False,
            "stdout": "",
            "stderr": f"[Timeout] command exceeded {timeout}s and was terminated",
            "code": 124,
            "mode": mode,
        }
    except asyncio.CancelledError:
        # Outer agent_tool_timeout_seconds cancelled us — must kill OS children.
        await kill_process_tree(proc)
        raise

    from backend.computer.text_decode import decode_process_bytes

    out = decode_process_bytes(stdout_b or b"")
    err = decode_process_bytes(stderr_b or b"")
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
