"""
Tool 执行器
内置工具的具体执行逻辑
"""

import asyncio
import glob
import json
import logging
import os
import re
import shlex
import sqlite3
import subprocess
import sys
import urllib.parse
from html import unescape
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _resolve_workspace_path(base_path: str, filepath: str) -> tuple[str, str]:
    """解析 workspace 内的安全路径。

    - 绝对路径：规范化后直接返回（由调用方做边界检查）
    - 相对路径：拼到 base_path；去掉重复的 workspace 前缀

    Returns:
        (full_path, base_abs) 元组
    """
    base_abs = os.path.abspath(base_path)
    raw = (filepath or "").strip()
    if not raw:
        return base_abs, base_abs

    # 绝对路径（POSIX / Windows 盘符）
    if os.path.isabs(raw) or (len(raw) >= 3 and raw[1] == ":" and raw[2] in "\\/"):
        return os.path.abspath(raw), base_abs

    fp = raw.replace("\\", "/").lstrip("/")
    basename = os.path.basename(base_abs.rstrip("/\\"))
    bp_rel = base_path.replace("\\", "/").rstrip("/").lstrip("./")
    for prefix in {basename, bp_rel}:
        if prefix and fp.startswith(prefix + "/"):
            fp = fp[len(prefix) + 1 :]
            break
    full_path = os.path.abspath(os.path.join(base_abs, fp))
    return full_path, base_abs


# 安全命令白名单（仅允许这些命令名，不是前缀匹配）
_SAFE_COMMANDS: set[str] = {
    "ls", "cat", "head", "tail", "grep", "find", "pwd", "echo",
    "ps", "df", "du", "whoami", "uname", "date", "wc", "sort",
    "mkdir", "touch", "cp", "mv", "rm", "rmdir",
}

# 危险命令模式：命中则需前端弹窗确认后才执行。
# 设计：默认放开（python/pip/npm/git 等开发命令直接跑），仅真正危险的拦截确认。
_DANGEROUS_PATTERNS = [
    # 递归/强制删除
    (r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)?.+", "递归/强制删除文件"),
    (r"\bdel\s+/[fsq]", "强制删除文件 (Windows)"),
    (r"\brmdir\s+/s", "递归删除目录 (Windows)"),
    (r"Remove-Item\s+.*-Recurse", "递归删除 (PowerShell)"),
    # 系统级
    (r"\bsudo\b", "提权执行"),
    (r"\bshutdown\b|\breboot\b|\bpoweroff\b", "关机/重启"),
    (r"\bmkfs\b|\bfdisk\b|\bdd\s+if=", "磁盘操作"),
    (r"\bformat\s+[a-zA-Z]:", "格式化磁盘 (Windows)"),
    # 注册表 / 服务
    (r"\breg\s+(delete|add)\b", "修改注册表"),
    (r"\bsc\s+(delete|stop|config)\b", "修改系统服务"),
    (r"\bnet\s+(stop|user|localgroup)\b", "网络/账户管理"),
    (r"\btaskkill\s+/f", "强制结束进程"),
    # 远程脚本执行
    (r"(curl|wget)[^|]*\|\s*(sh|bash|zsh|python)", "远程脚本管道执行"),
    # 写系统目录
    (r"[>]\s*/etc/|[>]\s*/usr/|[>]\s*C:\\\\Windows", "写入系统目录"),
    (r"\bchmod\s+(-R\s+)?777\b", "放开文件权限 777"),
]

# 硬禁止：空字节。换行已放开（支持 cat <<EOF heredoc）；反引号放开（与 Hermes 对齐）。
# 危险操作仍走 _DANGEROUS_PATTERNS + 前端确认。
_FORBIDDEN_SUBSTR = ["\x00"]


async def execute_browser(config: dict[str, Any], arguments: dict[str, Any]) -> str:
    """浏览器工具：支持 action=fetch|navigate|snapshot|click|type|press|close。

    - fetch: aiohttp 拉 HTML（默认，无浏览器依赖）
    - 其余: Playwright 自动化（未安装则提示 + fallback fetch）
    """
    action = str(arguments.get("action") or "fetch").strip().lower()
    url = (arguments.get("url") or "").strip()
    timeout = int(arguments.get("timeout") or config.get("timeout") or 30)
    selector = (arguments.get("selector") or "").strip()
    text = arguments.get("text") or arguments.get("value") or ""
    key = (arguments.get("key") or "").strip()
    session_key = str(arguments.get("session") or "default")

    if action in ("fetch", "get", "read") or (action == "navigate" and not _playwright_available()):
        if not url:
            return "[Error] url is required for fetch/navigate"
        return await _browser_fetch(url, timeout=timeout)

    # Playwright path
    try:
        return await _browser_playwright(
            action=action,
            url=url,
            selector=selector,
            text=str(text),
            key=key,
            timeout=timeout,
            session_key=session_key,
        )
    except Exception as e:
        if url and action in ("navigate", "open"):
            fb = await _browser_fetch(url, timeout=timeout)
            return f"[Playwright unavailable: {e}]\nFell back to fetch:\n{fb}"
        return f"[Error] browser action failed: {e}"


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except Exception:
        return False


async def _browser_fetch(url: str, *, timeout: int = 30) -> str:
    user_agent = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    try:
        import aiohttp

        headers = {"User-Agent": user_agent}
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                text = await resp.text()
                if len(text) > 12000:
                    text = text[:12000] + "\n...[truncated]"
                return f"Status: {resp.status}\nURL: {resp.url}\n\n{text}"
    except ImportError:
        return "[Error] aiohttp is not installed"
    except Exception as e:
        return f"[Error] {e}"


# Playwright 会话（进程内复用）
_PW_STATE: dict[str, Any] = {"pw": None, "browser": None, "contexts": {}}


async def _browser_playwright(
    *,
    action: str,
    url: str,
    selector: str,
    text: str,
    key: str,
    timeout: int,
    session_key: str,
) -> str:
    from playwright.async_api import async_playwright

    if _PW_STATE["pw"] is None:
        _PW_STATE["pw"] = await async_playwright().start()
        _PW_STATE["browser"] = await _PW_STATE["pw"].chromium.launch(headless=True)
        _PW_STATE["contexts"] = {}

    contexts = _PW_STATE["contexts"]
    if session_key not in contexts:
        ctx = await _PW_STATE["browser"].new_context()
        page = await ctx.new_page()
        contexts[session_key] = {"ctx": ctx, "page": page}
    page = contexts[session_key]["page"]
    page.set_default_timeout(max(5000, timeout * 1000))

    if action in ("close",):
        try:
            await contexts[session_key]["ctx"].close()
        finally:
            contexts.pop(session_key, None)
        return f"[browser] session {session_key} closed"

    if action in ("sessions", "list_sessions"):
        keys = list(contexts.keys())
        return f"browser sessions: {keys or ['(none)']}"

    if action in ("navigate", "open", "goto"):
        if not url:
            return "[Error] url required"
        await page.goto(url, wait_until="domcontentloaded")
        title = await page.title()
        return f"[navigated] {page.url}\ntitle: {title}\nsession={session_key}"

    if action in ("snapshot", "content", "text"):
        if url:
            await page.goto(url, wait_until="domcontentloaded")
        title = await page.title()
        body = await page.inner_text("body")
        if len(body) > 12000:
            body = body[:12000] + "\n...[truncated]"
        links = await page.eval_on_selector_all(
            "a[href]",
            "els => els.slice(0,30).map(a => ({text:(a.innerText||'').trim().slice(0,80), href:a.href}))",
        )
        buttons = await page.eval_on_selector_all(
            "button, [role=button], input[type=submit]",
            "els => els.slice(0,20).map(b => (b.innerText||b.value||'').trim().slice(0,60)).filter(Boolean)",
        )
        a11y = ""
        try:
            snap = await page.accessibility.snapshot()

            def _walk(n, depth=0, acc=None):
                if acc is None:
                    acc = []
                if not n or depth > 6 or len(acc) > 80:
                    return acc
                role = n.get("role") or ""
                name = (n.get("name") or "")[:60]
                if role or name:
                    acc.append(f"{'  ' * depth}{role}: {name}")
                for c in n.get("children") or []:
                    _walk(c, depth + 1, acc)
                return acc

            a11y = "\n".join(_walk(snap)[:80])
        except Exception as e:
            a11y = f"(a11y unavailable: {e})"
        return (
            f"URL: {page.url}\nTitle: {title}\nsession={session_key}\n"
            f"Buttons: {buttons}\nLinks: {links}\n"
            f"--- a11y ---\n{a11y}\n\n--- body text ---\n{body}"
        )

    if action == "click":
        if not selector:
            return "[Error] selector required for click"
        await page.click(selector)
        return f"[clicked] {selector} @ {page.url}"

    if action in ("type", "fill"):
        if not selector:
            return "[Error] selector required for type"
        await page.fill(selector, text)
        return f"[typed] into {selector} ({len(text)} chars)"

    if action == "press":
        target = selector or "body"
        await page.press(target, key or "Enter")
        return f"[pressed] {key or 'Enter'} on {target}"

    if action == "screenshot":
        import base64

        raw = await page.screenshot(type="jpeg", quality=60, full_page=False)
        b64 = base64.b64encode(raw).decode("ascii")
        return (
            f"[screenshot] url={page.url} session={session_key} jpeg_base64_len={len(b64)}\n"
            f"data:image/jpeg;base64,{b64[:200]}...[omitted]"
        )

    return (
        f"[Error] unknown action={action}. "
        "Use fetch|navigate|snapshot|click|type|press|screenshot|sessions|close"
    )



async def execute_process(config: dict[str, Any], arguments: dict[str, Any]) -> str:
    """后台进程轮询/列表/终止。"""
    from backend.services.tools import process_registry as preg

    action = str(arguments.get("action") or "list").strip().lower()
    pid = str(arguments.get("process_id") or arguments.get("id") or "").strip()

    if action == "list":
        items = preg.list_processes()
        if not items:
            return "No background processes."
        return json.dumps(items, ensure_ascii=False, indent=2)

    if not pid:
        return "[Error] process_id required"
    p = preg.get_process(pid)
    if p is None:
        return f"[Error] process not found: {pid}"

    if action in ("poll", "status", "log"):
        return preg.format_process(p)

    if action in ("kill", "stop"):
        if p.proc and not p.done:
            try:
                p.proc.kill()
            except Exception as e:
                return f"[Error] kill failed: {e}"
        return f"[killed] {pid} done={p.done} exit={p.exit_code}"

    return f"[Error] unknown action={action}. Use list|poll|kill"


async def execute_list_devices(config: dict[str, Any], arguments: dict[str, Any]) -> str:
    """列出已配对远程设备（+ 本机 local 虚拟设备）。"""
    user_id = arguments.get("_user_id") or arguments.get("user_id")
    lines = [
        "Devices:",
        "- local | online | type=self | 本机（command 工具直接执行）",
    ]
    try:
        from backend.repositories.device_repo import AsyncDeviceRepository
        from backend.services.remote.transport import transport_from_device_config

        repo = AsyncDeviceRepository()
        devices = []
        if user_id:
            import uuid as _uuid

            uid = user_id if isinstance(user_id, _uuid.UUID) else _uuid.UUID(str(user_id))
            devices = await repo.list_by_user(uid) or []
        else:
            # best-effort: list all if repo supports
            try:
                devices = await repo.list_all() or []  # type: ignore[attr-defined]
            except Exception:
                devices = []

        if not devices:
            lines.append(
                "(no paired remote devices — pair takton-agent via /devices or POST /api/devices/pair)"
            )
            return "\n".join(lines)

        for d in devices:
            online = "?"
            try:
                tr = transport_from_device_config(getattr(d, "config", None) or {})
                tr.timeout_s = 3.0
                await tr.ping()
                online = "online"
            except Exception:
                online = "offline"
            lines.append(
                f"- {getattr(d, 'name', '?')} | {online} | type={getattr(d, 'device_type', '?')} | "
                f"status={getattr(d, 'status', '')}"
            )
        lines.append("Remote exec: remote_exec(device=NAME, command=...) or chat @NAME cmd")
        return "\n".join(lines)
    except Exception as e:
        lines.append(f"(device list error: {e})")
        return "\n".join(lines)


async def execute_remote_exec(config: dict[str, Any], arguments: dict[str, Any]) -> str:
    """在配对设备上执行 command / list / read（对标 OpenClaw node exec）。"""
    device_name = (
        arguments.get("device")
        or arguments.get("device_name")
        or arguments.get("name")
        or ""
    ).strip()
    command = (arguments.get("command") or arguments.get("cmd") or "").strip()
    action = str(arguments.get("action") or "exec").strip().lower()
    path = (arguments.get("path") or "").strip()
    user_id = arguments.get("_user_id") or arguments.get("user_id")

    if not device_name:
        return "[Error] device name required"
    if device_name.lower() in ("local", "localhost", "self", "本机"):
        # 本机走 command
        if action in ("list", "ls"):
            return await execute_command(
                config,
                {"command": f'ls -la "{path or "."}"' if os.name != "nt" else f'dir "{path or "."}"'},
            )
        if action == "read":
            return await execute_file_read(config, {"filepath": path or "."})
        return await execute_command(config, {"command": command, "timeout": arguments.get("timeout", 45)})

    if not user_id:
        return "[Error] user context missing for remote device lookup"

    try:
        import uuid as _uuid

        from backend.api.routes.devices import resolve_device_by_name
        from backend.repositories.device_repo import AsyncDeviceRepository
        from backend.services.remote.transport import RemoteAgentError, transport_from_device_config

        uid = user_id if isinstance(user_id, _uuid.UUID) else _uuid.UUID(str(user_id))
        repo = AsyncDeviceRepository()
        device = await resolve_device_by_name(repo, uid, device_name)
        if device is None:
            return (
                f"[Error] device «{device_name}» not found. "
                "Pair takton-agent at /devices first. list_devices to see names."
            )
        tr = transport_from_device_config(device.config or {})
        tr.timeout_s = float(arguments.get("timeout") or 45)
        if action in ("list", "ls"):
            result = await tr.call("file.list", {"path": path or "."})
            return json.dumps(result, ensure_ascii=False, indent=2)
        if action == "read":
            if not path:
                return "[Error] path required for read"
            result = await tr.call("file.read", {"path": path})
            content = (result or {}).get("content", "")
            if len(content) > 12000:
                content = content[:12000] + "\n...[truncated]"
            return content or json.dumps(result, ensure_ascii=False)
        if not command:
            return "[Error] command required for exec"
        result = await tr.call("exec.run", {"command": command})
        code = result.get("exit_code")
        out = (result.get("stdout") or "").strip()
        err = (result.get("stderr") or "").strip()
        parts = [f"@{device.name} exit={code}", f"$ {command}"]
        if out:
            parts.append(out)
        if err:
            parts.append(f"[stderr]\n{err}")
        return "\n".join(parts)
    except RemoteAgentError as e:
        return f"[RemoteError] {e.message}"
    except Exception as e:
        return f"[Error] remote_exec failed: {e}"



import re


def _match_dangerous(command: str) -> str | None:
    """检测命令是否命中危险模式，返回危险原因（None=安全）。"""
    for pattern, reason in _DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return reason
    return None


async def execute_command(config: dict[str, Any], arguments: dict[str, Any]) -> str:
    """
    命令行工具：执行 shell 命令（P0 增强：cwd / 更长超时 / 输出截断 / 后台）。

    安全模型（v3.1）：
    1. 默认放开：python/pip/npm/node/git、管道、重定向、&&、多行 heredoc。
    2. 仅真正危险的操作触发前端弹窗确认。
    3. 多行写文件仍推荐 file_write/edit；command 支持 heredoc 但不鼓励用 shell 拼大文件。
    4. 默认 cwd = workspace root（可用参数 cwd 覆盖）。
    """
    command = arguments.get("command", "").strip()
    if not command:
        return "[Error] command is required"

    if "\x00" in command:
        return "[Security Blocked] NUL bytes are not allowed in command"

    danger_reason = _match_dangerous(command)
    if danger_reason:
        from backend.services import confirm_manager

        ws_manager = arguments.get("_ws_manager")
        session_id = arguments.get("_session_id")
        approved = await confirm_manager.request_confirmation(
            ws_manager,
            session_id,
            title="危险操作确认",
            command=command,
            reason=danger_reason,
        )
        if not approved:
            return (
                f"[Denied] Dangerous command was rejected by user "
                f"({danger_reason}): {command}"
            )

    timeout = int(arguments.get("timeout") or config.get("timeout") or 120)
    timeout = max(1, min(timeout, 600))
    max_output = int(arguments.get("max_output") or config.get("max_output") or 50000)
    max_output = max(1000, min(max_output, 200_000))
    cwd = (
        arguments.get("cwd")
        or arguments.get("working_dir")
        or config.get("working_dir")
        or config.get("base_path")
    )
    if not cwd:
        try:
            from backend.tools.permissions import resolve_agent_workspace_root
            cwd = resolve_agent_workspace_root()
        except Exception:
            cwd = os.getcwd()
    cwd = os.path.abspath(str(cwd))
    if not os.path.isdir(cwd):
        return f"[Error] cwd does not exist: {cwd}"

    background = bool(arguments.get("background") or arguments.get("bg"))
    if background:
        from backend.services.tools.process_registry import start_background, format_process

        item = await start_background(command, cwd=cwd)
        # give a short moment for quick commands
        await asyncio.sleep(0.15)
        return (
            f"[Background started] id={item.id}\n"
            f"Use process tool action=poll process_id={item.id} to check output.\n"
            + format_process(item, tail=2000)
        )

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd if cwd else None,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        if len(out) > max_output:
            out = out[:max_output] + f"\n...[stdout truncated {len(stdout)} bytes]"
        if len(err) > max_output // 2:
            err = err[: max_output // 2] + f"\n...[stderr truncated {len(stderr)} bytes]"
        out, err = out.strip(), err.strip()
        header = f"[Exit {proc.returncode}" + (f" cwd={cwd}" if cwd else "") + "]"
        if err:
            return f"{header}\nstdout:\n{out or '(empty)'}\n\nstderr:\n{err}"
        return out or f"{header}\n[No output]"
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return f"[Timeout] Command exceeded {timeout}s and was terminated"
    except FileNotFoundError:
        return f"[Error] Command not found: {command.split()[0] if command else ''}"
    except Exception as e:
        return f"[Error] {e}"


async def execute_file_read(config: dict[str, Any], arguments: dict[str, Any]) -> str:
    """
    文件读取工具：读取指定文件内容
    """
    filepath = arguments.get("filepath", "")
    if not filepath:
        return "[Error] filepath is required"

    base_path = config.get("base_path", "./workspace")
    full_path, base_abs = _resolve_workspace_path(base_path, filepath)

    # 路径安全检查：防止目录遍历
    try:
        Path(full_path).resolve().relative_to(Path(base_abs).resolve())
    except Exception:
        if not (full_path == base_abs or full_path.startswith(base_abs + os.sep)):
            return f"[Security Blocked] Path '{filepath}' is outside the allowed directory"

    if not os.path.exists(full_path):
        return f"[Error] File not found: {filepath}"
    if not os.path.isfile(full_path):
        return f"[Error] Not a file: {filepath}"

    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if len(content) > 20000:
            content = content[:20000] + "\n...[truncated]"
        return content
    except Exception as e:
        return f"[Error] {e}"


async def execute_file_write(config: dict[str, Any], arguments: dict[str, Any]) -> str:
    """
    文件写入工具：写入内容到指定文件
    """
    filepath = arguments.get("filepath", "")
    content = arguments.get("content", "")
    if not filepath:
        return "[Error] filepath is required"

    base_path = config.get("base_path", "./workspace")
    full_path, base_abs = _resolve_workspace_path(base_path, filepath)

    # 路径安全检查
    try:
        Path(full_path).resolve().relative_to(Path(base_abs).resolve())
    except Exception:
        if not (full_path == base_abs or full_path.startswith(base_abs + os.sep)):
            return f"[Security Blocked] Path '{filepath}' is outside the allowed directory"

    # 确保目录存在
    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    try:
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"[Success] Written {len(content)} characters to {filepath}"
    except Exception as e:
        return f"[Error] {e}"


async def execute_http(config: dict[str, Any], arguments: dict[str, Any]) -> str:
    """
    HTTP 请求工具：发送 HTTP 请求
    支持自定义工具的 HTTP API 调用
    """
    method = (arguments.get("method") or config.get("method", "GET")).upper()
    url = arguments.get("url", "")
    if not url:
        # 自定义工具可能把 URL 放在 config 中
        url = config.get("url", "")
    if not url:
        return "[Error] url is required"

    timeout = config.get("timeout", 30)
    headers = {**(config.get("headers") or {}), **(arguments.get("headers") or {})}
    body = arguments.get("body") or arguments.get("data")

    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            req_kwargs = {
                "headers": headers,
                "timeout": aiohttp.ClientTimeout(total=timeout),
            }
            if body and method in ("POST", "PUT", "PATCH"):
                if isinstance(body, dict):
                    req_kwargs["json"] = body
                else:
                    req_kwargs["data"] = body

            async with session.request(method, url, **req_kwargs) as resp:
                text = await resp.text()
                if len(text) > 12000:
                    text = text[:12000] + "\n...[truncated]"
                return f"Status: {resp.status}\nURL: {resp.url}\n\n{text}"
    except ImportError:
        return "[Error] aiohttp is not installed"
    except Exception as e:
        return f"[Error] {e}"


async def execute_python(config: dict[str, Any], arguments: dict[str, Any]) -> str:
    """
    Python 代码执行工具：在受限环境中执行 Python 代码
    使用 subprocess 执行，有超时限制
    """
    code = arguments.get("code", "").strip()
    if not code:
        return "[Error] code is required"

    timeout = arguments.get("timeout", config.get("timeout", 30))

    # 安全模型 v3：放开 subprocess/os.system（agent 装依赖需要），
    # 仅真正危险的系统级代码触发前端确认。
    danger_reason = None
    danger_code_patterns = [
        (r"os\.system\([^)]*(rm\s+-[rf]|del\s+/[fsq]|format|mkfs|shutdown|reboot)", "危险系统命令"),
        (r"shutil\.rmtree\s*\(\s*['\"][/A-Za-z]:", "递归删除根目录"),
        (r"subprocess[^)]*(rm\s+-rf|del\s+/f|format|mkfs|shutdown)", "危险子进程命令"),
    ]
    for pattern, reason in danger_code_patterns:
        if re.search(pattern, code):
            danger_reason = reason
            break

    if danger_reason:
        from backend.services import confirm_manager

        approved = await confirm_manager.request_confirmation(
            arguments.get("_ws_manager"),
            arguments.get("_session_id"),
            title="危险操作确认",
            command=code[:300],
            reason=danger_reason,
        )
        if not approved:
            return f"[Denied] Dangerous python code was rejected by user ({danger_reason})"

    # Prefer current interpreter (Windows rarely has python3 on PATH)
    py = sys.executable or "python3"
    try:
        proc = await asyncio.create_subprocess_exec(
            py, "-c", code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()
        if err:
            return f"[Exit {proc.returncode}]\nstdout:\n{out}\n\nstderr:\n{err}"
        return out or "[No output]"
    except asyncio.TimeoutError:
        return f"[Timeout] Execution exceeded {timeout}s"
    except Exception as e:
        return f"[Error] {e}"


async def _search_duckduckgo(
    query: str, max_results: int, headers: dict[str, str]
) -> list[str]:
    """DuckDuckGo HTML 搜索"""
    import aiohttp
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            html = await resp.text()

    results = []
    blocks = re.split(r'<div class="result[^"]*"[^>]*>', html)[1:]
    for block in blocks[:max_results]:
        title_match = re.search(
            r'<a[^>]*class="result__a"[^>]*>(.*?)</a>', block, re.DOTALL
        )
        title = (
            re.sub(r"<[^>]+>", "", unescape(title_match.group(1))).strip()
            if title_match else "No title"
        )
        snippet_match = re.search(
            r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', block, re.DOTALL
        )
        snippet = (
            re.sub(r"<[^>]+>", "", unescape(snippet_match.group(1))).strip()
            if snippet_match else ""
        )
        url_match = re.search(
            r'<a[^>]*class="result__url"[^>]*href="([^"]*)"', block
        )
        result_url = unescape(url_match.group(1)) if url_match else ""
        results.append(f"{len(results) + 1}. {title}\n   {result_url}\n   {snippet}")
    return results


async def _search_bing(
    query: str, max_results: int, headers: dict[str, str]
) -> list[str]:
    """Bing 搜索作为备选"""
    import aiohttp
    url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}"
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            html = await resp.text()

    results = []
    # Bing 结果在 <li class="b_algo"> 中
    blocks = re.split(r'<li class="b_algo"[^>]*>', html)[1:]
    for block in blocks[:max_results]:
        title_match = re.search(r'<h2[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if title_match:
            result_url = unescape(title_match.group(1))
            title = re.sub(r"<[^>]+>", "", unescape(title_match.group(2))).strip()
        else:
            title_match2 = re.search(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', block, re.DOTALL)
            if title_match2:
                result_url = unescape(title_match2.group(1))
                title = re.sub(r"<[^>]+>", "", unescape(title_match2.group(2))).strip()
            else:
                continue

        snippet_match = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
        snippet = (
            re.sub(r"<[^>]+>", "", unescape(snippet_match.group(1))).strip()
            if snippet_match else ""
        )
        results.append(f"{len(results) + 1}. {title}\n   {result_url}\n   {snippet}")
    return results


async def execute_search(config: dict[str, Any], arguments: dict[str, Any]) -> str:
    """
    网络搜索工具：免 API Key 瀑布（ddgs / DDG / Bing / Wikipedia）。
    可选 config.engine: auto|ddgs|duckduckgo|bing|wikipedia
    """
    query = (arguments.get("query") or "").strip()
    if not query:
        return "[Error] query is required"

    max_results = int(arguments.get("max_results", config.get("max_results", 5)) or 5)
    engine = str(config.get("engine") or arguments.get("engine") or "auto").lower()

    try:
        from backend.services.tools.free_search import (
            free_web_search,
            search_bing_html,
            search_ddg_html,
            search_ddg_lite,
            search_ddgs,
            search_wikipedia,
            _fmt,
        )
    except Exception as e:
        return f"[Error] free_search module unavailable: {e}"

    if engine == "auto":
        return await free_web_search(query, max_results)

    mapping = {
        "ddgs": search_ddgs,
        "duckduckgo": search_ddg_html,
        "ddg": search_ddg_html,
        "ddg-lite": search_ddg_lite,
        "bing": search_bing_html,
        "wikipedia": search_wikipedia,
        "wiki": search_wikipedia,
    }
    fn = mapping.get(engine)
    if not fn:
        return await free_web_search(query, max_results)
    try:
        rows, eng = await fn(query, max_results)
        if rows:
            return _fmt(rows, query, eng)
        return f"No results found. (engine={eng})"
    except Exception as e:
        return f"[Error] Search failed ({engine}): {e}"


async def execute_edit(config: dict[str, Any], arguments: dict[str, Any]) -> str:
    """
    文件编辑工具：在现有文件中精确替换字符串
    类似 Claude Code 的 Edit 工具
    """
    filepath = arguments.get("filepath", "")
    old_text = arguments.get("old_text", "")
    new_text = arguments.get("new_text", "")

    if not filepath or old_text == "":
        return "[Error] filepath and old_text are required"

    base_path = config.get("base_path", "./workspace")
    full_path, base_abs = _resolve_workspace_path(base_path, filepath)

    # 路径安全检查
    if not full_path.startswith(base_abs):
        return (
            f"[Security Blocked] Path '{filepath}' is outside the allowed directory"
        )

    if not os.path.exists(full_path):
        return f"[Error] File not found: {filepath}"
    if not os.path.isfile(full_path):
        return f"[Error] Not a file: {filepath}"

    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        if old_text not in content:
            return f"[Error] old_text not found in {filepath}"

        new_content = content.replace(old_text, new_text, 1)

        with open(full_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return (
            f"[Success] Edited {filepath}: "
            f"replaced {len(old_text)} chars with {len(new_text)} chars"
        )
    except Exception as e:
        return f"[Error] {e}"


async def execute_glob(config: dict[str, Any], arguments: dict[str, Any]) -> str:
    """
    文件搜索工具：使用通配符模式匹配文件
    类似 Claude Code 的 Glob 工具
    """
    pattern = arguments.get("pattern", "")
    if not pattern:
        return "[Error] pattern is required"

    base_path = config.get("base_path", "./workspace")
    base_abs = os.path.abspath(base_path)

    # 防止目录遍历
    if ".." in pattern:
        return "[Security Blocked] Pattern cannot contain '..'"

    search_path = os.path.join(base_abs, pattern)

    try:
        matches = glob.glob(search_path, recursive=True)
        rel_matches = []
        for m in sorted(matches):
            m_abs = os.path.abspath(m)
            if not m_abs.startswith(base_abs):
                continue
            if os.path.isfile(m):
                rel_matches.append(os.path.relpath(m, base_abs))

        if not rel_matches:
            return "No files matched."
        return f"Matched {len(rel_matches)} file(s):\n" + "\n".join(rel_matches)
    except Exception as e:
        return f"[Error] {e}"


async def execute_grep(config: dict[str, Any], arguments: dict[str, Any]) -> str:
    """
    文本搜索工具：在文件或目录中搜索匹配正则表达式的行
    类似 Claude Code 的 Grep 工具
    """
    pattern = arguments.get("pattern", "")
    path = arguments.get("path", "")
    recursive = arguments.get("recursive", True)

    if not pattern or not path:
        return "[Error] pattern and path are required"

    base_path = config.get("base_path", "./workspace")
    target_path, base_abs = _resolve_workspace_path(base_path, path)

    # 路径安全检查
    if not target_path.startswith(base_abs):
        return (
            f"[Security Blocked] Path '{path}' is outside the allowed directory"
        )

    if not os.path.exists(target_path):
        return f"[Error] Path not found: {path}"

    try:
        regex = re.compile(pattern)
        matches = []

        if os.path.isfile(target_path):
            files = [target_path]
        elif os.path.isdir(target_path) and recursive:
            files = []
            for root, _, filenames in os.walk(target_path):
                for filename in filenames:
                    files.append(os.path.join(root, filename))
        else:
            return f"[Error] {path} is not a file or directory"

        for filepath in files:
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if regex.search(line):
                            rel_path = os.path.relpath(filepath, base_abs)
                            matches.append(f"{rel_path}:{i}: {line.rstrip()}")
                            if len(matches) >= 100:
                                break
                if len(matches) >= 100:
                    break
            except (UnicodeDecodeError, IsADirectoryError, PermissionError):
                continue

        if not matches:
            return "No matches found."
        header = f"Found {len(matches)} match(es) (showing up to 100):\n"
        return header + "\n".join(matches)
    except re.error as e:
        return f"[Error] Invalid regex pattern: {e}"
    except Exception as e:
        return f"[Error] {e}"


async def execute_sqlite_query(
    config: dict[str, Any], arguments: dict[str, Any]
) -> str:
    """
    SQLite 查询工具：执行 SQL 查询
    支持 SELECT / INSERT / UPDATE / DELETE / CREATE 等
    """
    database = arguments.get("database", "")
    query = arguments.get("query", "").strip()

    if not database or not query:
        return "[Error] database and query are required"

    base_path = config.get("base_path", "./workspace")
    db_path, base_abs = _resolve_workspace_path(base_path, database)

    # 路径安全检查
    if not db_path.startswith(base_abs):
        return (
            f"[Security Blocked] Database path '{database}' "
            f"is outside the allowed directory"
        )

    def _run_query() -> str:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query)

        upper = query.split(None, 1)[0].upper()
        if upper in ("SELECT", "PRAGMA", "WITH", "EXPLAIN"):
            rows = cursor.fetchall()
            if not rows:
                conn.close()
                return "Query executed successfully. No rows returned."

            headers = rows[0].keys()
            lines = []
            lines.append(" | ".join(headers))
            lines.append("-" * len(lines[0]))
            for row in rows[:50]:
                lines.append(" | ".join(str(v) if v is not None else "NULL" for v in row))
            if len(rows) > 50:
                lines.append(f"... ({len(rows) - 50} more rows)")
            conn.close()
            return "\n".join(lines)
        else:
            conn.commit()
            affected = cursor.rowcount
            conn.close()
            return f"Query executed successfully. Rows affected: {affected}"

    try:
        return await asyncio.to_thread(_run_query)
    except sqlite3.Error as e:
        return f"[Error] SQLite error: {e}"
    except Exception as e:
        return f"[Error] {e}"


# 执行器映射
EXECUTOR_MAP = {
    "browser": execute_browser,
    "command": execute_command,
    "file_read": execute_file_read,
    "file_write": execute_file_write,
    "http": execute_http,
    "python": execute_python,
    "search": execute_search,
    "edit": execute_edit,
    "glob": execute_glob,
    "grep": execute_grep,
    "sqlite_query": execute_sqlite_query,
    "process": execute_process,
    "list_devices": execute_list_devices,
    "remote_exec": execute_remote_exec,
}
