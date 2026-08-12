"""
Tool 执行器
内置工具的具体执行逻辑
"""

import asyncio
import contextlib
import glob
import json
import logging
import os
import re
import sqlite3
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


def _is_within(path: str, base: str) -> bool:
    """path 是否在 base 之内（含 base 本身）。

    不能用 `path.startswith(base)`：base=/home/u/workspace 时
    /home/u/workspace-backup/secrets 会通过。必须按路径分量比较，
    并且 resolve() 掉符号链接，否则 workspace 里一个指向 / 的软链就破功。
    """
    try:
        p = Path(path).resolve()
        b = Path(base).resolve()
    except OSError:
        p, b = Path(os.path.abspath(path)), Path(os.path.abspath(base))
    try:
        p.relative_to(b)
        return True
    except ValueError:
        return False


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
    # 数据外泄类（提示词注入放大风险：读凭证/上传/反弹连接/编码外发）
    (r"(curl|wget)[^|]*(-d\s|--data|--data-binary|-F\s|--upload-file|-T\s)", "疑似文件上传/数据外泄"),
    (r"\b(nc|ncat|netcat)\b.{0,40}\d{2,5}\b", "疑似反弹/外发连接"),
    (r"\.ssh/(id_rsa|id_ed25519|id_ecdsa)|\.aws/credentials|\.config/gcloud", "读取云凭证/私钥文件"),
    (r"base64\s+[^|]*\|\s*(curl|wget|nc|ncat)", "疑似编码后外发"),
    (r"\bscp\s+.*@|\brsync\s+.*@", "疑似远程文件传输"),
    # 写系统目录
    (r"[>]\s*/etc/|[>]\s*/usr/|[>]\s*C:\\\\Windows", "写入系统目录"),
    (r"\bchmod\s+(-R\s+)?777\b", "放开文件权限 777"),
]

# ---- 高危命令分类（权限控制台的三态控制粒度，2026-07-26）----
# 每类可独立配置：allow（直接放行）/ confirm（每次弹窗确认）/ deny（硬禁止）
COMMAND_CATEGORIES: dict[str, str] = {
    "delete": "删除文件/目录",
    "privilege": "提权执行（sudo）",
    "power": "关机/重启",
    "disk": "磁盘操作（格式化/分区）",
    "system": "系统服务/注册表/账户管理",
    "remote_pipe": "远程脚本管道执行",
    "exfiltration": "数据外泄（上传/反弹/凭证读取）",
    "system_write": "写入系统目录/放开权限",
}

# label → category（_DANGEROUS_PATTERNS 保持二元组结构，分类经此映射）
_LABEL_TO_CATEGORY: dict[str, str] = {
    "递归/强制删除文件": "delete",
    "强制删除文件 (Windows)": "delete",
    "递归删除目录 (Windows)": "delete",
    "递归删除 (PowerShell)": "delete",
    "提权执行": "privilege",
    "关机/重启": "power",
    "磁盘操作": "disk",
    "格式化磁盘 (Windows)": "disk",
    "修改注册表": "system",
    "修改系统服务": "system",
    "网络/账户管理": "system",
    "强制结束进程": "system",
    "远程脚本管道执行": "remote_pipe",
    "疑似文件上传/数据外泄": "exfiltration",
    "疑似反弹/外发连接": "exfiltration",
    "读取云凭证/私钥文件": "exfiltration",
    "疑似编码后外发": "exfiltration",
    "疑似远程文件传输": "exfiltration",
    "写入系统目录": "system_write",
    "放开文件权限 777": "system_write",
}

# 内容层高严重度子集（evolution G2 等"检查文本内容"场景共用）：
# 只含破坏性 + 数据外泄类，不含 sudo/rm 等文档语境常见词，避免误杀教学性内容。
CONTENT_SEVERE_PATTERNS = [
    (pattern, label)
    for pattern, label in _DANGEROUS_PATTERNS
    if label
    in {
        "磁盘操作",
        "格式化磁盘 (Windows)",
        "修改注册表",
        "远程脚本管道执行",
        "疑似文件上传/数据外泄",
        "疑似反弹/外发连接",
        "读取云凭证/私钥文件",
        "疑似编码后外发",
        "疑似远程文件传输",
        "写入系统目录",
    }
]

# 硬禁止：空字节（见 execute_command）。换行已放开（支持 cat <<EOF heredoc）；
# 反引号放开（与 Hermes 对齐）。危险操作仍走 _DANGEROUS_PATTERNS + 前端确认。
AGENT_COMMAND_AUTO_BG_SECONDS = 15


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


def _guard_agent_url(url: str, *, tool: str) -> str | None:
    """Agent 联网工具的准入检查。返回 None = 放行，字符串 = 拒绝理由。

    分层策略见 core/net_safety.check_agent_url：只硬拦云元数据端点，
    私网/回环放行但记审计 —— 让 Agent 看 localhost:3000 或 NAS 是本地优先
    产品的核心用法，照搬服务端 SSRF 防护会把它拦死。
    """
    try:
        from backend.core.net_safety import check_agent_url
    except Exception:  # 防护模块不可用时不静默放行
        return "[Security Blocked] 网络准入模块不可用，已拒绝出站请求"

    allowed, note = check_agent_url(url)
    if not allowed:
        logger.warning("%s blocked: %s", tool, note)
        return f"[Security Blocked] {note}"
    if note:
        # 私网访问留痕，便于事后追溯「Agent 那天到底摸了内网的什么」
        logger.info("%s %s", tool, note)
    return None


async def _browser_fetch(url: str, *, timeout: int = 30) -> str:
    blocked = _guard_agent_url(url, tool="browser")
    if blocked:
        return blocked
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
        blocked = _guard_agent_url(url, tool="browser")
        if blocked:
            return blocked
        await page.goto(url, wait_until="domcontentloaded")
        title = await page.title()
        return f"[navigated] {page.url}\ntitle: {title}\nsession={session_key}"

    if action in ("snapshot", "content", "text"):
        if url:
            blocked = _guard_agent_url(url, tool="browser")
            if blocked:
                return blocked
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
        import json as _json
        import os
        import tempfile
        import time
        from pathlib import Path as _Path

        raw = await page.screenshot(type="jpeg", quality=60, full_page=False)
        out_dir = os.environ.get("TEVARN_BROWSER_SHOT_DIR") or os.path.join(
            tempfile.gettempdir(), "tevarn_browser_shots"
        )
        os.makedirs(out_dir, exist_ok=True)
        out_path = str(_Path(out_dir) / f"browser_{int(time.time() * 1000)}.jpg")
        with open(out_path, "wb") as f:
            f.write(raw)
        return _json.dumps(
            {
                "ok": True,
                "path": out_path,
                "bytes": len(raw),
                "url": page.url,
                "session": session_key,
                "image": "",
            },
            ensure_ascii=False,
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
        try:
            return preg.poll_process_throttled(p)
        except Exception:
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
                "(no paired remote devices — pair tevarn-agent via /devices or POST /api/devices/pair)"
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

    # 转发给其它执行器时必须带上内部 meta（_ws_manager / _session_id），
    # 否则下游的危险操作确认推不到前端，只会静默超时后报「用户已拒绝」。
    _meta = {k: v for k, v in arguments.items() if str(k).startswith("_")}

    if device_name.lower() in ("local", "localhost", "self", "本机"):
        # 本机走 command（execute_command 内部已过 enforce_command_policy）
        if action in ("list", "ls"):
            listing = (
                f'ls -la "{path or "."}"' if os.name != "nt" else f'dir "{path or "."}"'
            )
            return await execute_command(config, {**_meta, "command": listing})
        if action == "read":
            return await execute_file_read(config, {**_meta, "filepath": path or "."})
        return await execute_command(
            config,
            {**_meta, "command": command, "timeout": arguments.get("timeout", 45)},
        )

    if not user_id:
        return "[Error] user context missing for remote device lookup"

    try:
        import uuid as _uuid

        from backend.api.routes.devices import resolve_device_by_name
        from backend.repositories.device_repo import AsyncDeviceRepository
        from backend.services.remote.transport import (
            RemoteAgentError,
            transport_from_device_config,
        )

        uid = user_id if isinstance(user_id, _uuid.UUID) else _uuid.UUID(str(user_id))
        repo = AsyncDeviceRepository()
        device = await resolve_device_by_name(repo, uid, device_name)
        if device is None:
            return (
                f"[Error] device «{device_name}» not found. "
                "Pair tevarn-agent at /devices first. list_devices to see names."
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
        # 远程执行同样要过权限控制台的三态策略 —— 命令跑在别人的机器上不代表
        # 危险性变低，而用户配置的规则本就该覆盖「Agent 能发起的所有执行」。
        blocked = await enforce_command_policy(
            command, arguments, where=f"@{device.name}"
        )
        if blocked:
            return blocked
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



def should_use_sandbox() -> bool:
    """本次命令是否走隔离后端。

    单一事实源：working_mode.decide_sandbox()。
    agent_computer_enabled=True 表示「优先隔离」（默认开启）；execution_mode=local 仍可显式本机。
    无可用沙箱且 mode=auto 时返回 False（本机 + degraded），不再无脑 True 导致必失败。
    """
    try:
        from backend.agent.working_mode import decide_sandbox, resolve_execution_mode
        from backend.core.config import settings as _cs

        mode = resolve_execution_mode()
        if mode == "local":
            return False
        decision = decide_sandbox()
        if mode == "sandbox":
            return True
        # auto：有能力才用；computer_enabled 只影响「愿不愿意尝试」
        if bool(getattr(_cs, "agent_computer_enabled", True)):
            return bool(decision.use_sandbox)
        return False
    except Exception:
        return False


def _kernel_process_id_from_args(arguments: dict[str, Any]) -> str | None:
    """从工具参数 / recorder 取 kernel process id（隔离与 Court 共用）。"""
    pid = str(
        arguments.get("_kernel_process_id")
        or arguments.get("_process_id")
        or ""
    ).strip()
    if pid:
        return pid
    rec = arguments.get("_run_recorder")
    if rec is not None:
        pid = str(getattr(rec, "kernel_process_id", "") or "").strip()
        if pid:
            return pid
    return None


def _isolation_sandbox_required(process_id: str | None, agent_key: str = "") -> bool:
    """Rust isolation profile 是否强制沙箱（workforce / untrusted）。"""
    if not process_id:
        return False
    try:
        from backend.kernel import get_kernel

        k = get_kernel()
        is_wf = str(agent_key or "").startswith("wf:")
        if hasattr(k, "isolation_resolve"):
            pol = k.isolation_resolve(process_id, is_workforce=is_wf) or {}
        elif hasattr(k, "_call"):
            pol = (
                k._call(
                    "isolation_resolve",
                    {"process_id": process_id, "is_workforce": is_wf},
                )
                or {}
            )
        else:
            return False
        return bool(pol.get("sandbox_required"))
    except Exception:
        return False


def _must_use_computer_path(arguments: dict[str, Any]) -> bool:
    """P0 gap-fill：UI 选 local 时仍可被 isolation profile 强制走 ComputerManager。"""
    if should_use_sandbox():
        return True
    pid = _kernel_process_id_from_args(arguments)
    akey = str(arguments.get("_agent_key") or "main")
    return _isolation_sandbox_required(pid, akey)


def _match_dangerous(command: str) -> str | None:
    """检测命令是否命中危险模式，返回危险原因（None=安全）。

    定位说明（T5）：这是一条**便利提示**，不是安全边界。
    正则黑名单的绕过成本极低（`$(printf '\\x72\\x6d')`、base64 管道、变量拼接…），
    真正的边界是执行环境里的沙箱（见 working_mode.decide_sandbox）。
    保留它是为了在用户误敲高危命令时给一次确认机会，不要当作防护依赖。
    """
    for pattern, reason in _DANGEROUS_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return reason
    return None


def _match_dangerous_full(command: str) -> tuple[str, str] | None:
    """命中返回 (危险原因, 分类 id)；分类缺失时兜底 system。"""
    reason = _match_dangerous(command)
    if reason is None:
        return None
    return reason, _LABEL_TO_CATEGORY.get(reason, "system")


async def enforce_command_policy(
    command: str,
    arguments: dict[str, Any],
    *,
    where: str = "",
) -> str | None:
    """高危命令的三态策略闸门。返回 None = 放行，返回字符串 = 拒绝理由。

    **所有会执行 shell 命令的路径都必须过这里**，否则用户在权限控制台配的规则
    就是骗人的。此前 remote_exec 直接把命令透传给配对设备，`_DANGEROUS_PATTERNS`、
    八类三态配置、确认弹窗全都不适用 —— 换个 device 参数就能绕开整个 /security 面板。

    Args:
        where: 执行位置描述（如 "@nas"），只用于给用户的提示文案。
    """
    danger_reason = _match_dangerous(command)
    if not danger_reason:
        return None

    from backend.core.command_policy import get_category_action

    category = _LABEL_TO_CATEGORY.get(danger_reason, "system")
    action = await get_category_action(category)
    scope = f"（执行位置：{where}）" if where else ""

    if action == "deny":
        return (
            f"[Policy Blocked] 该命令属于「{category}」高危类别{scope}，"
            f"已在权限控制台被设为禁止（原因：{danger_reason}）。"
            f"如需执行，请在权限控制台将该类别改为「每次确认」或「放行」: {command}"
        )
    if action == "allow":
        return None

    # 员工工单：危险命令由编制策略裁决，绝不弹主人确认窗
    try:
        from backend.agent.steward_permission import (
            is_workforce_context,
            steward_decide_tool,
        )

        if is_workforce_context(arguments):
            decision, why = await steward_decide_tool("command", arguments)
            if decision == "allow":
                logger = __import__("logging").getLogger(__name__)
                logger.info(
                    "workforce dangerous cmd steward-allow reason=%s cmd=%s",
                    danger_reason,
                    command[:120],
                )
                return None
            return (
                f"[steward deny] 危险命令未执行（{danger_reason}）{scope}：{why}。"
                f"命令：{command}"
            )
    except Exception:
        pass

    # 本轮 tool_hooks 已确认过（含 once）→ 不再二次弹窗
    # 只信服务端标记：模型注入的 _confirm_ok 在 _validate_tool_args 已剥离
    if arguments.get("_confirm_ok") is True and arguments.get("_confirm_ok_source") == "server":
        return None

    # 「本会话允许」短路：与 tool_hooks / grant_store 对齐
    try:
        from backend.agent.grant_store import has_session_grant

        sid = str(arguments.get("_session_id") or "")
        if sid and has_session_grant(sid, "command", arguments):
            return None
    except Exception:
        pass

    # 「本员工允许」短路：编制能力已含 command → 危险类别也不再弹
    try:
        from backend.agent.grant_store import has_identity_tool_grant

        if await has_identity_tool_grant("command", arguments=arguments):
            logger = __import__("logging").getLogger(__name__)
            logger.info(
                "command policy identity_cap skip confirm reason=%s cmd=%s",
                danger_reason,
                command[:120],
            )
            arguments["_confirm_ok"] = True
            arguments["_confirm_ok_source"] = "server"
            return None
    except Exception:
        pass

    # confirm（默认）：主人主会话走前端确认（once / session / agent）
    from backend.agent.grant_store import (
        add_session_grant,
        grant_agent_capability,
        resolve_identity_id,
    )
    from backend.services import confirm_manager

    agent_id = await resolve_identity_id(arguments)
    agent_name = (
        str(arguments.get("_identity_name") or arguments.get("agent_name") or arguments.get("_contact_agent") or "").strip()
        or None
    )

    outcome = await confirm_manager.request_confirmation(
        arguments.get("_ws_manager"),
        arguments.get("_session_id"),
        title="危险操作确认",
        command=f"{where} $ {command}" if where else command,
        reason=danger_reason,
        tool="command",
        agent_id=agent_id,
        agent_name=agent_name,
        user_id=str(
            arguments.get("_user_id") or arguments.get("user_id") or ""
        ).strip()
        or None,
    )
    if outcome:
        conf_scope = getattr(outcome, "scope", "once") or "once"
        sid = str(arguments.get("_session_id") or "") or None
        if conf_scope == "session":
            add_session_grant(sid, "command", arguments)
        elif conf_scope == "agent":
            if not agent_id:
                agent_id = await resolve_identity_id(arguments, contact_name=agent_name)
            await grant_agent_capability(agent_id, "command")
            # 整工具会话缓存，避免只记住 command:rm
            add_session_grant(sid, "command", arguments, whole_tool=True)
        arguments["_confirm_ok"] = True
        arguments["_confirm_ok_source"] = "server"
        return None

    # 诚实口径：没问到人 ≠ 用户拒绝。前者要让模型知道是环境问题，
    # 别把「确认通道不通」当成用户意图去改写策略或反复重试。
    label = "Denied" if outcome.reason == "denied" else "Blocked"
    return (
        f"[{label}] 危险命令未执行（{danger_reason}）{scope}："
        f"{outcome.describe()}。命令：{command}"
    )


# Rust tool policy (Windows Job sandbox):
# - rustup: always blocked when *invoked* (not when mentioned in `where rustup`)
# - cargo: allowlist check/build/test/… + version probes
# - rustc: version probes only
# - Do NOT block `where cargo` / `dir …\cargo.exe` — false positives caused 复读
# JobBackend injects MSVC vcvars + healthy RUSTUP_HOME for allowed cargo.
_CARGO_ALLOW_SUBCMD_RE = re.compile(
    r"(?i)(?P<exe>(?:[\"']?)(?:[A-Za-z]:[\\/][^\s\"']*[\\/])?cargo(?:\.exe)?[\"']?)\s+"
    r"(?:check|build|test|metadata|tree|clippy|fmt|doc|fetch|update|clean|report|"
    r"(?:-|--)?version|-V)\b"
)
_CARGO_DENY_SUBCMD_RE = re.compile(
    r"(?i)\bcargo(?:\.exe)?\s+"
    r"(?:install|uninstall|publish|login|logout|owner|yank|package|new|init|"
    r"add|remove|search|fix|vendor|miri|rustc)\b"
)
_RUSTC_VERSION_ONLY_RE = re.compile(
    r"(?i)\brustc(?:\.exe)?\s+(?:-|--)?(?:version|vV|V)\b"
    r"|\brustc(?:\.exe)?\s+-vV\b"
)
# Real executable token — must NOT match env vars RUSTUP_HOME / RUSTUP_TOOLCHAIN
# (false positive caused agent 复读: blocked set RUSTUP_* → more diagnosis).
_TOOL_TOKEN_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?P<tool>rustup|cargo|rustc)(?:\.exe)?(?![A-Za-z0-9_])"
)


def _strip_lookup_mentions(command: str) -> str:
    """Remove where/Get-Command/dir path probes so policy only sees real invokes."""
    c = command or ""
    c = re.sub(
        r"(?i)\bwhere(?:\.exe)?(?:\s+/[A-Za-z]+)*\s+(?:[A-Za-z]:\\[^\s&|;]*)?\s*"
        r"[^\s&|;]*\b(?:cargo|rustc|rustup)(?:\.exe)?[^\s&|;]*",
        " ",
        c,
    )
    c = re.sub(
        r"(?i)\bGet-Command\s+[^\s&|;]*\b(?:cargo|rustc|rustup)(?:\.exe)?[^\s&|;]*",
        " ",
        c,
    )
    c = re.sub(
        r"(?i)\bwhere(?:\.exe)?\s+[^\s&|;]*\b(?:cargo|rustc|rustup)(?:\.exe)?\b",
        " ",
        c,
    )
    # Env assignments are not invocations: RUSTUP_HOME=... / set RUSTUP_TOOLCHAIN=...
    c = re.sub(
        r"(?i)(?:set\s+)?(?:RUSTUP_HOME|RUSTUP_TOOLCHAIN|CARGO_HOME|RUSTFLAGS)\s*=\s*[^\s&|;]*",
        " ",
        c,
    )
    return c


def _invoked_rust_tools(command: str) -> set[str]:
    """Tools that appear as executables to run (not path/lookup/env text)."""
    c = _strip_lookup_mentions(command)
    # dir/ls of paths containing cargo.exe — not an invoke of cargo
    if re.search(r"(?i)\b(?:dir|ls|Get-ChildItem|type|Get-Content)\b", c):
        if not re.search(
            r"(?i)(?<![A-Za-z0-9_])(?:cargo|rustc|rustup)(?:\.exe)?(?![A-Za-z0-9_])\s+",
            c,
        ):
            return set()
    found: set[str] = set()
    for m in _TOOL_TOKEN_RE.finditer(c):
        # Skip if this token is clearly a path segment (...\cargo\bin) without running
        start = m.start()
        # path like \cargo\ or /cargo/
        if start > 0 and c[start - 1] in "\\/" and (
            m.end() < len(c) and c[m.end()] in "\\/"
        ):
            continue
        found.add(m.group("tool").lower())
    return found


def _block_agent_rust_tools(command: str) -> str | None:
    """Return block message, or None if the command may run.

    Policy: rustup always blocked when invoked; cargo check/build/test/version
    allowed; bare rustc compile blocked. Lookups like ``where cargo`` allowed.
    """
    c = (command or "").strip()
    if not c:
        return None

    tools = _invoked_rust_tools(c)
    if not tools:
        return None

    if "rustup" in tools:
        try:
            from backend.agent.progress_guard import cargo_blocked_hint, resolve_project_anchor

            _hint = cargo_blocked_hint(resolve_project_anchor() or "tavarn-guardian")
        except Exception:
            _hint = "请用 command：`cargo check -p guardian-server`。"
        return (
            "[Blocked] rustup 禁止在 agent 内执行（曾导致 rustc 0xc0000409 拖垮窗口）。"
            + _hint
        )

    if _CARGO_DENY_SUBCMD_RE.search(c):
        try:
            from backend.agent.progress_guard import cargo_blocked_hint, resolve_project_anchor

            _hint = cargo_blocked_hint(resolve_project_anchor() or "tavarn-guardian")
        except Exception:
            _hint = "请用 `cargo check` / `cargo build` / `cargo test`。"
        return (
            "[Blocked] 该 cargo 子命令禁止在 agent 内执行（install/publish/new 等）。"
            + _hint
        )

    if "cargo" in tools:
        if _CARGO_ALLOW_SUBCMD_RE.search(c):
            return None
        # cargo with no subcommand or unknown — still allow bare `cargo --version`
        if re.search(r"(?i)\bcargo(?:\.exe)?\s*$", c.strip()):
            return (
                "[Blocked] 请指定子命令，例如 `cargo check` / `cargo build` / `cargo -V`。"
            )
        return (
            "[Blocked] cargo 仅放行：check / build / test / metadata / tree / clippy / "
            "fmt / doc / fetch / update / clean / -V。"
            f"当前：{c[:120]}"
        )

    if "rustc" in tools:
        if _RUSTC_VERSION_ONLY_RE.search(c):
            return None
        return (
            "[Blocked] 裸 rustc 编译禁止；请用 `cargo check` / `cargo build`。"
            "版本：`rustc -vV`。"
        )

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
    # Windows 沙箱 JobBackend 走 cmd.exe：裸 echo/dir 有时无输出；统一给可诊断失败信息

    if "\x00" in command:
        return "[Security Blocked] NUL bytes are not allowed in command"

    _rust_block = _block_agent_rust_tools(command)
    if _rust_block:
        return _rust_block

    # Normalize before sandbox: strip double cmd /c and rewrite PowerShell cmdlets.
    # Fixes: '\"rustc --version & ...\"' 不是内部或外部命令
    try:
        import sys as _sys

        if _sys.platform == "win32":
            from backend.core.safe_subprocess import (
                expand_safe_windows_env,
                normalize_windows_job_command,
            )

            _exp = expand_safe_windows_env(command)
            if _exp != command:
                command = _exp
                arguments = dict(arguments)
                arguments["command"] = command
            _norm = normalize_windows_job_command(command)
            if _norm != command:
                __import__("logging").getLogger(__name__).info(
                    "normalized windows command: %s → %s",
                    command[:80],
                    _norm[:80],
                )
                command = _norm
                arguments = dict(arguments)
                arguments["command"] = command
    except Exception as _norm_e:
        __import__("logging").getLogger(__name__).debug(
            "command normalize skip: %s", _norm_e
        )

    # P0：编制/本机 command 中的 python 强制项目 venv（避免 PATH 上 hermes 等污染）
    try:
        from backend.core.project_python import rewrite_command_python

        command, _rewrote = rewrite_command_python(command)
        if _rewrote:
            arguments = dict(arguments)
            arguments["command"] = command
    except Exception as _py_e:
        __import__("logging").getLogger(__name__).debug(
            "python rewrite skip: %s", _py_e
        )

    blocked = await enforce_command_policy(command, arguments)
    if blocked:
        return blocked

    # Default 90s; hard-cap by outer agent_tool_timeout so we never schedule
    # work that the agent loop will cancel first (was 600 → outer 180 first-win).
    try:
        from backend.core.config import settings as _st

        _outer = float(getattr(_st, "agent_tool_timeout_seconds", 180) or 180)
    except Exception:
        _outer = 180.0
    _cap = max(15, int(_outer) - 5) if _outer > 0 else 90
    timeout = int(arguments.get("timeout") or config.get("timeout") or 90)
    timeout = max(1, min(timeout, _cap, 180))
    max_output = int(arguments.get("max_output") or config.get("max_output") or 50000)
    max_output = max(1000, min(max_output, 200_000))
    cwd_raw = (
        arguments.get("cwd")
        or arguments.get("working_dir")
        or config.get("working_dir")
        or config.get("base_path")
    )
    try:
        from backend.tools.permissions import resolve_agent_workspace_root

        _ws_root = resolve_agent_workspace_root() or os.getcwd()
    except Exception:
        _ws_root = os.getcwd()
    _ws_root = os.path.abspath(str(_ws_root))

    # Relative cwd must join workspace root — NOT process CWD (install resources/).
    # Fixes: cwd=tavarn-guardian → Programs\tevarn\resources\tavarn-guardian
    if not cwd_raw:
        cwd = _ws_root
    else:
        _cr = str(cwd_raw).strip().strip('"')
        if os.path.isabs(_cr):
            cwd = os.path.abspath(_cr)
        else:
            try:
                from backend.core.config import settings as _st_cwd

                _ws_rel = bool(getattr(_st_cwd, "agent_cwd_workspace_relative", True))
            except Exception:
                _ws_rel = True
            if _ws_rel:
                cwd = os.path.abspath(os.path.join(_ws_root, _cr))
            else:
                cwd = os.path.abspath(_cr)

    # Never use install-tree fake project as cwd when real workspace exists elsewhere
    try:
        from backend.core.config import settings as _st_inst

        if bool(getattr(_st_inst, "agent_block_install_tree_cwd", True)):
            _norm = os.path.normcase(cwd)
            if (
                "programs" in _norm
                and "tevarn" in _norm
                and "resources" in _norm
                and "appdata" not in _norm
            ):
                # Prefer real workspace / project anchor under Roaming data
                from backend.agent.progress_guard import resolve_project_anchor as _rpa_i

                _alt = _rpa_i(_ws_root) or _ws_root
                if os.path.isdir(_alt) and os.path.normcase(
                    os.path.abspath(_alt)
                ) != _norm:
                    cwd = os.path.abspath(_alt)
    except Exception:
        pass

    if not os.path.isdir(cwd):
        return f"[Error] cwd does not exist: {cwd}"
    # Cargo at monorepo parent without Cargo.toml → project anchor (tavarn-guardian/…)
    try:
        from backend.agent.progress_guard import (
            is_cargo_verify_command as _is_cvc_cwd,
            resolve_project_anchor as _rpa_cwd,
        )

        if _is_cvc_cwd(command) and not os.path.isfile(os.path.join(cwd, "Cargo.toml")):
            _anchor = _rpa_cwd(_ws_root) or _rpa_cwd(cwd)
            if (
                _anchor
                and os.path.isdir(_anchor)
                and os.path.isfile(os.path.join(_anchor, "Cargo.toml"))
                and os.path.normcase(os.path.abspath(_anchor))
                != os.path.normcase(cwd)
            ):
                cwd = os.path.abspath(_anchor)
    except Exception:
        pass

    background = bool(arguments.get("background") or arguments.get("bg"))
    # 长命令自动后台：构建/安装/测试/训练等不阻塞 agent loop
    _auto_bg = bool(arguments.get("auto_bg", True))
    # cargo check/fmt/clippy usually finish in seconds–minutes; auto-bg caused
    # process-poll thrash + doom_loop before results arrived. Keep them foreground
    # (timeout→bg still applies later). Only auto-bg heavy cargo test/build when
    # explicitly long timeout.
    _cargo_light = bool(
        re.search(
            r"(?i)\bcargo(?:\.exe)?\s+(?:check|fmt|clippy|metadata|tree)\b",
            command or "",
        )
    )
    _cargo_heavy = bool(
        re.search(
            r"(?i)\bcargo(?:\.exe)?\s+(?:test|build|bench)\b",
            command or "",
        )
    )
    _long_hint = re.search(
        r"(pytest|py\.test|unittest|pip3?\s+install|npm\s+(install|ci|run|build)|"
        r"pnpm\s+(install|add|run)|yarn\s+(add|install|build)|"
        r"cargo\s+(?:test|build|bench)\b|go\s+(test|build)|mvn\s+|gradle\s+|"
        r"make\s+(-j)?|cmake\s+|docker\s+(build|compose|pull)|"
        r"sleep\s+[5-9]|sleep\s+\d{2,}|curl\s+.*-o\s|wget\s+|"
        r"python\s+-m\s+(pytest|http\.server|uvicorn)|"
        r"train|finetune|download)",
        command,
        re.I,
    )
    # Auto-background only for *clearly* long work.
    # Do NOT use default timeout alone (default is ~90s) — that wrongly
    # backgrounded trivial `cmd /c dir` and caused empty process thrash.
    # Never auto-bg light cargo (check/fmt/clippy) — doom_loop on poll thrash.
    if _cargo_light and not bool(arguments.get("background") or arguments.get("bg")):
        background = False
        _auto_bg = False
    if (
        not background
        and _auto_bg
        and _long_hint
        and timeout >= int(AGENT_COMMAND_AUTO_BG_SECONDS)
        and not _cargo_light
    ):
        # cargo test/build: only auto-bg when timeout is large (≥90)
        if _cargo_heavy and timeout < 90:
            background = False
        else:
            background = True
            logger = __import__("logging").getLogger(__name__)
            logger.info("auto-background long command: %s", command[:120])
    elif (
        not background
        and _auto_bg
        and timeout >= 120
        and re.search(r"(install|build|test|train|download|compose)", command, re.I)
        and not _cargo_light
    ):
        background = True
        logger = __import__("logging").getLogger(__name__)
        logger.info("auto-background high-timeout command: %s", command[:120])

    if background:
        from backend.services.tools.process_registry import (
            format_process,
            start_background,
        )

        _sid_bg = str(arguments.get("_session_id") or "").strip() or None
        item = await start_background(command, cwd=cwd, session_id=_sid_bg)
        # Hybrid await: wait for completion up to a cap so agent gets real
        # stderr/exit without process-poll thrash. Instant fails still return
        # immediately with diagnostics.
        _wait_cap = 0.25
        try:
            if re.search(
                r"(?i)\bcargo(?:\.exe)?\s+(?:check|clippy)\b",
                command or "",
            ):
                _wait_cap = float(min(max(timeout, 15), 90))
            elif re.search(
                r"(?i)\bcargo(?:\.exe)?\s+(?:test|build|bench)\b",
                command or "",
            ):
                _wait_cap = float(min(max(timeout, 30), 150))
            elif re.search(
                r"(?i)\bcargo(?:\.exe)?\s+",
                command or "",
            ):
                _wait_cap = 3.0
        except Exception:
            _wait_cap = 0.25
        _t0 = __import__("time").monotonic()
        while not item.done and (__import__("time").monotonic() - _t0) < _wait_cap:
            await asyncio.sleep(0.5 if _wait_cap >= 3 else 0.15)
        # If already done, return full format so agent sees stderr / Finished
        if item.done:
            return (
                f"[Background finished] id={item.id}\n"
                f"(no need to process poll — result below)\n"
                + format_process(item, tail=max(2000, min(max_output, 12000)))
            )
        return (
            f"[Background started] id={item.id}\n"
            f"Still running after {_wait_cap:.0f}s await; "
            f"Use process tool action=poll process_id={item.id} "
            f"(interval ≥8s) or wait for [bg_complete] auto-inject.\n"
            f"Do NOT spam empty polls — that triggers doom_loop.\n"
            + format_process(item, tail=2000)
        )

    # 执行环境裁决（T5）：sandbox / auto；P0：isolation profile 可强制 ComputerManager
    # （即便 UI 设 local，workforce/untrusted 仍不得绕过沙箱账本与策略）
    _kpid = _kernel_process_id_from_args(arguments)
    if _must_use_computer_path(arguments):
        try:
            from backend.computer.manager import get_computer_manager

            _res = await get_computer_manager().execute(
                command,
                agent_key=str(arguments.get("_agent_key") or "main"),
                agent_label=str(arguments.get("_agent_label") or ""),
                session_id=arguments.get("_session_id"),
                recorder=arguments.get("_run_recorder"),
                cwd=cwd,
                timeout=timeout,
                max_output=max_output,
                process_id=_kpid,
            )
            return await _maybe_cargo_stub_retry(
                _res,
                command=command,
                cwd=cwd,
                timeout=timeout,
                max_output=max_output,
                arguments=arguments,
                _kpid=_kpid,
            )
        except Exception as _ce:
            # 安全口径：用户要的是隔离，静默降级到本机直跑会破坏预期。
            # 编制/强制沙箱：统一 workforce fail-closed 文案。
            __import__("logging").getLogger(__name__).warning(
                "agent computer execute failed: %s", _ce
            )
            akey = str(arguments.get("_agent_key") or "main")
            if (
                akey.startswith("wf:")
                or bool(arguments.get("_workforce"))
                or _isolation_sandbox_required(_kpid, akey)
            ):
                try:
                    from backend.kernel.tool_gate import workforce_sandbox_fail_message

                    return workforce_sandbox_fail_message(
                        profile_id="workforce",
                        detail=str(_ce),
                    )
                except Exception:
                    pass
            return (
                f"[Error] 沙箱执行失败: {_ce}"
                "（未降级本机直跑。可在权限控制台把「执行环境」改为「自动」或「本机直跑」）"
            )

    # 无强制隔离时仍尽量登记 isolation_spawn（local 账本），失败不阻断
    if _kpid:
        try:
            from backend.kernel import get_kernel

            k = get_kernel()
            if hasattr(k, "_acall"):
                # audit-fix: async 上下文走 _acall，避免阻塞事件循环
                h = await k._acall(
                    "isolation_spawn",
                    {
                        "process_id": _kpid,
                        "command": command[:500],
                        "backend": "local",
                    },
                )
                # 若 profile 拒绝 local，fail closed
                if isinstance(h, dict) and h.get("error"):
                    return f"[Error] isolation denied: {h.get('error')}"
        except Exception as _iso_e:
            msg = str(_iso_e)
            if "isolation" in msg.lower() or "sandbox" in msg.lower() or "local" in msg.lower():
                return f"[Error] isolation denied: {_iso_e}"

    # Foreground with Grok-style timeout→background (do not kill long work).
    try:
        from backend.core.safe_subprocess import create_process, needs_shell
        from backend.services.tools.process_registry import (
            adopt_running,
            format_process,
        )
        from backend.computer.text_decode import decode_process_bytes

        proc = await create_process(command, cwd=cwd if cwd else None)
        mode = "shell" if needs_shell(command) else "exec"
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=float(timeout)
            )
        except asyncio.TimeoutError:
            # Leave process running; model can process poll/kill
            try:
                item = await adopt_running(
                    proc,
                    command,
                    cwd=cwd,
                    session_id=str(arguments.get("_session_id") or "").strip() or None,
                )
            except Exception as _ad_e:
                from backend.core.safe_subprocess import kill_process_tree

                await kill_process_tree(proc)
                return (
                    f"[Timeout] Command exceeded {timeout}s and was terminated "
                    f"(background adopt failed: {_ad_e})"
                )
            return (
                f"[Background after timeout] id={item.id}\n"
                f"Command still running after {timeout}s (not killed).\n"
                f"Use process tool: action=poll process_id={item.id}\n"
                f"Then action=kill process_id={item.id} when done.\n"
                + format_process(item, tail=1500)
            )
        except asyncio.CancelledError:
            from backend.core.safe_subprocess import kill_process_tree

            await kill_process_tree(proc)
            raise

        out = decode_process_bytes(stdout_b or b"")
        err = decode_process_bytes(stderr_b or b"")
        if len(out) > max_output:
            out = out[:max_output] + f"\n...[stdout truncated]"
        if len(err) > max(max_output // 2, 1000):
            err = err[: max_output // 2] + f"\n...[stderr truncated]"
        code = proc.returncode if proc.returncode is not None else -1
        header = (
            f"[Exit {code}"
            + (f" cwd={cwd}" if cwd else "")
            + (f" mode={mode}" if mode else "")
            + "]"
        )
        out_s, err_s = out.strip(), err.strip()
        if err_s:
            _local_res = f"{header}\nstdout:\n{out_s or '(empty)'}\n\nstderr:\n{err_s}"
        elif out_s:
            _local_res = f"{header}\n{out_s}"
        elif code not in (0, None, "0"):
            _local_res = (
                f"{header}\n[No output] 命令无 stdout/stderr。"
                "Windows 请用 `cmd /c echo ok`、`cmd /c dir`、`where python`。"
            )
        else:
            _local_res = f"{header}\n[No output]"
        # Local path: stub auto-clean via shell if needed
        try:
            from backend.agent.progress_guard import is_metadata_stub_error
            from backend.core.config import settings as _st_lc

            if (
                bool(getattr(_st_lc, "agent_cargo_stub_auto_clean", True))
                and is_metadata_stub_error(_local_res)
                and re.search(r"(?i)\bcargo(?:\.exe)?\s+(?:check|build|test)\b", command or "")
            ):
                clean = "cargo clean"
                m = re.search(r"(?i)\s-p\s+(\S+)", command or "")
                if m:
                    clean = f"cargo clean -p {m.group(1)}"
                proc2 = await create_process(clean, cwd=cwd if cwd else None)
                await asyncio.wait_for(proc2.communicate(), timeout=min(float(timeout), 120.0))
                proc3 = await create_process(command, cwd=cwd if cwd else None)
                o3, e3 = await asyncio.wait_for(
                    proc3.communicate(), timeout=float(timeout)
                )
                retry_out = decode_process_bytes(o3 or b"") + decode_process_bytes(e3 or b"")
                return (
                    f"[auto] metadata stub → `{clean}` then retry.\n"
                    f"--- first ---\n{_local_res[:1000]}\n--- retry ---\n{retry_out[:max_output]}"
                )
        except Exception:
            pass
        return _local_res
    except FileNotFoundError:
        return f"[Error] Command not found: {command.split()[0] if command else ''}"
    except ValueError as e:
        return f"[Security Blocked] {e}"
    except NotImplementedError as e:
        # Windows SelectorEventLoop used to raise bare NotImplementedError from
        # asyncio.create_subprocess_*; create_process now falls back to Popen.
        msg = str(e).strip() or "asyncio subprocess not supported on this event loop"
        return (
            f"[Error] Command spawn NotImplementedError: {msg}. "
            "Backend should use threaded Popen fallback (safe_subprocess). "
            "Restart Tevarn/Takton if this persists after update; "
            "retry with `cargo -V` then `cargo check -p <crate>`."
        )
    except Exception as e:
        msg = str(e).strip() or type(e).__name__
        return f"[Error] {type(e).__name__}: {msg}"


async def _maybe_cargo_stub_retry(
    result: str,
    *,
    command: str,
    cwd: str | None,
    timeout: int,
    max_output: int,
    arguments: dict[str, Any],
    _kpid: str | None,
) -> str:
    """On rustc 'metadata stub' / broken target, cargo clean once then re-check."""
    try:
        from backend.core.config import settings as _st
        from backend.agent.progress_guard import is_metadata_stub_error

        if not bool(getattr(_st, "agent_cargo_stub_auto_clean", True)):
            return result
        if not is_metadata_stub_error(result or ""):
            return result
        if not re.search(r"(?i)\bcargo(?:\.exe)?\s+(?:check|build|test)\b", command or ""):
            return result
    except Exception:
        return result

    clean_cmd = "cargo clean"
    # Prefer package-scoped clean when -p present
    m = re.search(r"(?i)\s-p\s+(\S+)", command or "")
    if m:
        clean_cmd = f"cargo clean -p {m.group(1)}"
    try:
        from backend.computer.manager import get_computer_manager

        logger = __import__("logging").getLogger(__name__)
        logger.warning(
            "cargo metadata stub detected — auto %s then retry: %s",
            clean_cmd,
            (command or "")[:100],
        )
        await get_computer_manager().execute(
            clean_cmd,
            agent_key=str(arguments.get("_agent_key") or "main"),
            agent_label=str(arguments.get("_agent_label") or ""),
            session_id=arguments.get("_session_id"),
            recorder=arguments.get("_run_recorder"),
            cwd=cwd,
            timeout=min(int(timeout), 120),
            max_output=max_output,
            process_id=_kpid,
        )
        retry = await get_computer_manager().execute(
            command,
            agent_key=str(arguments.get("_agent_key") or "main"),
            agent_label=str(arguments.get("_agent_label") or ""),
            session_id=arguments.get("_session_id"),
            recorder=arguments.get("_run_recorder"),
            cwd=cwd,
            timeout=timeout,
            max_output=max_output,
            process_id=_kpid,
        )
        return (
            f"[auto] detected metadata stub → ran `{clean_cmd}` then retried.\n"
            f"--- first error (truncated) ---\n{(result or '')[:1200]}\n"
            f"--- retry result ---\n{retry}"
        )
    except Exception as e:
        return (
            f"{result}\n\n[auto] cargo clean retry failed: {e}. "
            "请手动：`cargo clean` 后 `cargo check`。"
        )


# file_read 分页参数（T3）
FILE_READ_DEFAULT_LIMIT = 1000        # 单次默认行数
FILE_READ_MAX_CHARS = 20_000          # 单次输出字符上限（按行边界收口）
FILE_READ_MAX_LINE_CHARS = 2_000      # 单行过长时的截断长度


def _file_read_char_budget() -> int:
    """分页上限须低于 tool_round 的有效上限，否则那里会再做一次 head+tail 拼接，
    把按行分好的视图重新打断（正是 T3 要消灭的故障）。

    有效上限 = max(settings.max_tool_result_length, TOOL_RESULT_BUDGET['file_read'])
    —— 与 tool_round 的计算保持一致。
    """
    try:
        from backend.core.config import settings as _s

        hard = int(getattr(_s, "max_tool_result_length", 12_000) or 12_000)
    except Exception:
        hard = 12_000
    try:
        from backend.agent.tool_result_contract import TOOL_RESULT_BUDGET

        hard = max(hard, int(TOOL_RESULT_BUDGET.get("file_read", 0) or 0))
    except Exception:
        pass
    # 留出 footer 续读提示的余量
    return max(2_000, min(FILE_READ_MAX_CHARS, hard - 1_000))


def _read_file_paginated(
    full_path: str, filepath: str, offset: int, limit: int, char_budget: int
) -> str:
    """同步读文件并渲染带行号的分页视图（在线程里跑，见 execute_file_read）。"""
    with open(full_path, "rb") as fb:
        head = fb.read(8000)
    if b"\x00" in head:
        return (
            f"[Error] {filepath} looks like a binary file. "
            f"Use the command tool (e.g. `file`, `xxd`) if you need to inspect it."
        )

    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    total = len(lines)
    if total == 0:
        return f"[Empty file] {filepath} has 0 lines."
    if offset > total:
        return (
            f"[Error] offset {offset} is past end of {filepath} "
            f"(file has {total} lines)."
        )

    start = offset - 1
    end = min(total, start + limit)

    out: list[str] = []
    used = 0
    stopped_on_chars = False
    for idx in range(start, end):
        raw = lines[idx].rstrip("\n").rstrip("\r")
        if len(raw) > FILE_READ_MAX_LINE_CHARS:
            raw = (
                raw[:FILE_READ_MAX_LINE_CHARS]
                + f"…[line truncated, {len(raw)} chars total]"
            )
        rendered = f"{idx + 1:6d}\t{raw}"
        # 按行边界收口，绝不半行截断
        if used + len(rendered) + 1 > char_budget and out:
            stopped_on_chars = True
            break
        out.append(rendered)
        used += len(rendered) + 1

    last_shown = start + len(out)
    body = "\n".join(out)

    if last_shown < total:
        reason = "char budget" if stopped_on_chars else "line limit"
        footer = (
            f"\n\n[{filepath}: showing lines {start + 1}-{last_shown} of {total} "
            f"(stopped on {reason}). Call file_read again with offset={last_shown + 1} "
            f"to continue.]"
        )
    else:
        footer = f"\n\n[{filepath}: lines {start + 1}-{last_shown} of {total} — end of file]"
    return body + footer


async def execute_file_read(config: dict[str, Any], arguments: dict[str, Any]) -> str:
    """
    文件读取工具：带行号读取，支持 offset/limit 分页（T3）。

    输出为 `cat -n` 风格：行号 + TAB + 正文。行号是展示前缀，不属于文件内容，
    模型写 edit 的 old_text 时不得带上（工具描述里已声明）。
    截断永远发生在行边界，并给出可续读的 offset，避免模型基于断裂视图改代码。
    """
    filepath = arguments.get("filepath", "") or arguments.get("path", "")
    if not filepath:
        return "[Error] filepath is required"
    try:
        from backend.core.config import settings as _st_fr
        from backend.agent.progress_guard import is_diag_junk_path

        if bool(getattr(_st_fr, "agent_ignore_diag_junk_paths", True)) and is_diag_junk_path(
            str(filepath)
        ):
            return (
                f"[Blocked] 忽略诊断垃圾路径：{filepath}。"
                "不要读 _cargo_*/_diag_*/_hello*；请 file_write 业务源码或 cargo check。"
            )
    except Exception:
        pass
    try:
        from backend.tools.permissions import rewrite_host_path_into_workspace

        filepath = rewrite_host_path_into_workspace(str(filepath))
    except Exception:
        pass

    # 约定：缺省 / None / 0 一律回落到默认值（模型常用 0 表达「从头开始」）；
    # 负数是明确的调用错误，报错而非静默兜底。
    try:
        offset = int(arguments.get("offset") or 1)
    except (TypeError, ValueError):
        return "[Error] offset must be an integer (1-based line number)"
    try:
        limit = int(arguments.get("limit") or FILE_READ_DEFAULT_LIMIT)
    except (TypeError, ValueError):
        return "[Error] limit must be an integer (number of lines)"
    if offset < 1:
        return "[Error] offset must be >= 1 (line numbers are 1-based)"
    if limit < 1:
        return "[Error] limit must be >= 1 (omit it to use the default)"

    base_path = config.get("base_path", "./workspace")
    full_path, base_abs = _resolve_workspace_path(base_path, filepath)

    # 路径安全检查：防止目录遍历（宿主数据根额外放行）
    if not _is_within(full_path, base_abs):
        try:
            from backend.tools.permissions import ToolPermissionManager

            if not ToolPermissionManager().is_path_allowed(full_path):
                return (
                    f"[Security Blocked] Path '{filepath}' is outside the allowed "
                    f"directory (workspace={base_abs})"
                )
        except Exception:
            return f"[Security Blocked] Path '{filepath}' is outside the allowed directory"

    if not os.path.exists(full_path):
        return f"[Error] File not found: {filepath}"
    if not os.path.isfile(full_path):
        return f"[Error] Not a file: {filepath}"

    try:
        # 阻塞 I/O 丢进线程，让同轮并发的 tool call 真正并行（T1b）
        return await asyncio.to_thread(
            _read_file_paginated,
            full_path,
            filepath,
            offset,
            limit,
            _file_read_char_budget(),
        )
    except Exception as e:
        return f"[Error] {e}"


async def execute_file_write(config: dict[str, Any], arguments: dict[str, Any]) -> str:
    """
    文件写入工具：写入内容到指定文件
    """
    filepath = arguments.get("filepath", "") or arguments.get("path", "")
    content = arguments.get("content", "")
    if not filepath:
        return "[Error] filepath is required"

    try:
        from backend.agent.progress_guard import is_diag_junk_path

        if is_diag_junk_path(str(filepath)):
            return (
                f"[Blocked] 禁止写诊断垃圾：{filepath}。"
                "请改 crates/.../*.rs 业务源码，不要 _snap/_diag/_cargo dump。"
            )
    except Exception:
        pass

    # Absolute path under workspace → relative (model often passes full Windows path)
    try:
        from backend.tools.permissions import rewrite_host_path_into_workspace

        filepath = rewrite_host_path_into_workspace(str(filepath))
    except Exception:
        pass

    try:
        from backend.agent.progress_guard import is_diag_junk_path

        if is_diag_junk_path(str(filepath)):
            return (
                f"[Blocked] 禁止写诊断垃圾：{filepath}。"
                "请改 crates/.../*.rs 业务源码。"
            )
    except Exception:
        pass

    base_path = config.get("base_path", "./workspace")
    full_path, base_abs = _resolve_workspace_path(base_path, filepath)

    # 路径安全检查
    if not _is_within(full_path, base_abs):
        return (
            f"[Security Blocked] Path '{filepath}' is outside the allowed directory "
            f"(workspace={base_abs}). Use a relative path like 'docs/foo.md'."
        )

    # 确保目录存在
    parent = os.path.dirname(full_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

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

    blocked = _guard_agent_url(url, tool="http")
    if blocked:
        return blocked

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

    # Python must not shell rustup / unrestricted cargo — use `command` tool so
    # JobBackend can inject MSVC vcvars. Allow only if code clearly is cargo
    # check/build/test (still prefer command tool).
    if re.search(r"(?i)\brustup\b", code):
        try:
            from backend.agent.progress_guard import cargo_blocked_hint, resolve_project_anchor

            _h = cargo_blocked_hint(resolve_project_anchor() or "tavarn-guardian")
        except Exception:
            _h = "请用 command：`cargo check -p guardian-server`。"
        return "[Blocked] python 内禁止 rustup。" + _h
    if re.search(
        r"(?i)(?:subprocess|os\.system|os\.popen|Popen|run\()\s*.{0,80}\b(?:cargo|rustc)\b"
        r"|(?:['\"])(?:cargo|rustc)(?:\.exe)?(?:['\"])"
        r"|\bcargo\s+"
        r"|\brustc\s+",
        code,
    ):
        # Allow python only for allowlisted cargo subcommands (no rustc compile)
        if re.search(
            r"(?i)\bcargo(?:\.exe)?\s+"
            r"(?:check|build|test|metadata|tree|clippy|fmt|doc|fetch|update|clean)\b",
            code,
        ) and not re.search(r"(?i)\brustc\b", code):
            pass  # allowed, but Job/python env may lack vcvars — still better via command
        else:
            try:
                from backend.agent.progress_guard import (
                    cargo_blocked_hint,
                    resolve_project_anchor,
                )

                _h = cargo_blocked_hint(resolve_project_anchor() or "tavarn-guardian")
            except Exception:
                _h = "请用 command：`cargo check -p guardian-server`。"
            return "[Blocked] python 内请勿直接调 rustc/非白名单 cargo。" + _h

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
        # 只信服务端确认标记（模型注入的 _confirm_ok 已在 validate 剥离）
        confirmed = (
            arguments.get("_confirm_ok") is True
            and arguments.get("_confirm_ok_source") == "server"
        )
        if not confirmed:
            try:
                from backend.agent.grant_store import has_session_grant

                sid = str(arguments.get("_session_id") or "")
                if sid and has_session_grant(sid, "python", arguments):
                    confirmed = True
                    arguments["_confirm_ok"] = True
                    arguments["_confirm_ok_source"] = "server"
            except Exception:
                pass
        if not confirmed:
            try:
                from backend.agent.grant_store import has_identity_tool_grant

                if await has_identity_tool_grant("python", arguments=arguments):
                    confirmed = True
                    arguments["_confirm_ok"] = True
                    arguments["_confirm_ok_source"] = "server"
            except Exception:
                pass

        if not confirmed:
            from backend.agent.grant_store import (
                add_session_grant,
                grant_agent_capability,
                resolve_identity_id,
            )
            from backend.services import confirm_manager

            agent_id = await resolve_identity_id(arguments)
            agent_name = (
                str(arguments.get("_identity_name") or arguments.get("_contact_agent") or "").strip()
                or None
            )
            outcome = await confirm_manager.request_confirmation(
                arguments.get("_ws_manager"),
                arguments.get("_session_id"),
                title="危险操作确认",
                command=code[:300],
                reason=danger_reason,
                tool="python",
                agent_id=agent_id,
                agent_name=agent_name,
                user_id=str(
                    arguments.get("_user_id") or arguments.get("user_id") or ""
                ).strip()
                or None,
            )
            if not outcome:
                label = "Denied" if outcome.reason == "denied" else "Blocked"
                return (
                    f"[{label}] 危险 Python 代码未执行（{danger_reason}）："
                    f"{outcome.describe()}"
                )
            scope = getattr(outcome, "scope", "once") or "once"
            sid = str(arguments.get("_session_id") or "") or None
            if scope == "session":
                add_session_grant(sid, "python", arguments)
            if scope == "agent":
                if not agent_id:
                    agent_id = await resolve_identity_id(arguments, contact_name=agent_name)
                await grant_agent_capability(agent_id, "python")
                add_session_grant(sid, "python", arguments, whole_tool=True)
            arguments["_confirm_ok"] = True
            arguments["_confirm_ok_source"] = "server"

    # Prefer current interpreter (Windows rarely has python3 on PATH)
    py = sys.executable or ("python" if os.name == "nt" else "python3")

    # Windows：shlex.quote 用单引号，cmd/CreateProcess 会报「文件名语法不正确」；
    # 多行 -c 也脆弱。统一写临时 .py 再 exec，argv 列表传参。
    # 脚本落在 workspace 内（.computers/_tmp），避免沙箱 cwd/可见性与系统 Temp 脱节。
    import tempfile
    from pathlib import Path

    script_path: str | None = None
    try:
        try:
            from backend.tools.permissions import resolve_agent_workspace_root

            tmp_dir = Path(resolve_agent_workspace_root()) / ".computers" / "_tmp"
        except Exception:
            tmp_dir = Path(tempfile.gettempdir()) / "tevarn_py"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, script_path = tempfile.mkstemp(
            suffix=".py", prefix="tevarn_py_", dir=str(tmp_dir)
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(code if code.endswith("\n") else code + "\n")
    except Exception as e:
        return f"[Error] cannot write temp script: {e}"

    try:
        # 执行环境裁决（T5）：python 与 command 走同一口径；P0 isolation 强制 sandbox
        _kpid = _kernel_process_id_from_args(arguments)
        if _must_use_computer_path(arguments):
            try:
                from backend.computer.manager import get_computer_manager

                # JobBackend = cmd.exe /c <command>：Windows 引号规则是 "" 转义，不是 \"
                if os.name == "nt":
                    def _cmd_quote(s: str) -> str:
                        if not s:
                            return '""'
                        # 含空格/特殊字符才加引号；内部 " → ""
                        if any(c in s for c in ' \t&|<>()^"'):
                            return '"' + s.replace('"', '""') + '"'
                        return s

                    _cmd = f"{_cmd_quote(py)} {_cmd_quote(script_path)}"
                else:
                    import shlex

                    _cmd = f"{shlex.quote(py)} {shlex.quote(script_path)}"
                return await get_computer_manager().execute(
                    _cmd,
                    agent_key=str(arguments.get("_agent_key") or "main"),
                    agent_label=str(arguments.get("_agent_label") or ""),
                    session_id=arguments.get("_session_id"),
                    recorder=arguments.get("_run_recorder"),
                    timeout=int(timeout or 30),
                    process_id=_kpid,
                )
            except Exception as _ce:
                __import__("logging").getLogger(__name__).warning(
                    "agent computer python failed: %s", _ce
                )
                return (
                    f"[Error] 沙箱执行失败: {_ce}"
                    "（未降级本机直跑。可在权限控制台把「执行环境」改为「自动」或「本机直跑」）"
                )

        proc = None  # audit-fix: spawn 本身失败时 Timeout 分支不得引用未绑定变量
        try:
            from backend.core.safe_subprocess import kill_process_tree

            # stdin DEVNULL + process group so outer cancel can tree-kill
            _spawn: dict = {
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
                "stdin": asyncio.subprocess.DEVNULL,
            }
            if sys.platform != "win32":
                _spawn["start_new_session"] = True
            proc = await asyncio.create_subprocess_exec(py, script_path, **_spawn)
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            out = stdout.decode("utf-8", errors="replace").strip()
            err = stderr.decode("utf-8", errors="replace").strip()
            if err:
                return f"[Exit {proc.returncode}]\nstdout:\n{out}\n\nstderr:\n{err}"
            return out or "[No output]"
        except asyncio.TimeoutError:
            if proc is not None:
                try:
                    from backend.core.safe_subprocess import kill_process_tree

                    await kill_process_tree(proc)
                except Exception:
                    try:
                        proc.kill()
                        await proc.wait()
                    except Exception:
                        pass
            return f"[Timeout] Execution exceeded {timeout}s"
        except asyncio.CancelledError:
            if proc is not None:
                try:
                    from backend.core.safe_subprocess import kill_process_tree

                    await kill_process_tree(proc)
                except Exception:
                    pass
            raise
        except Exception as e:
            return f"[Error] {e}"
    finally:
        if script_path:
            try:
                os.unlink(script_path)
            except OSError:
                pass


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
    网络搜索：Tavily(有 Key) 优先，否则免 Key 瀑布。
    可选 config.engine: auto|tavily|ddgs|duckduckgo|bing|wikipedia
    """
    query = (arguments.get("query") or "").strip()
    if not query:
        return "[Error] query is required"

    max_results = int(arguments.get("max_results", config.get("max_results", 5)) or 5)
    engine = str(config.get("engine") or arguments.get("engine") or "auto").lower()

    if engine in ("auto", "tavily"):
        try:
            from backend.services.tools.web_search_unified import (
                tavily_search,
                web_search_unified,
            )
            if engine == "tavily":
                tv = await tavily_search(query, max_results, timeout=8.0)
                return tv or "[Error] Tavily failed or TAVILY_API_KEY missing"
            return await web_search_unified(query, max_results)
        except Exception as e:
            if engine == "tavily":
                return f"[Error] tavily: {e}"

    try:
        from backend.services.tools.free_search import (
            _fmt,
            free_web_search,
            search_bing_html,
            search_ddg_html,
            search_ddg_lite,
            search_ddgs,
            search_wikipedia,
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

    唯一性契约（T2）：old_text 必须在文件中唯一，否则报错而非静默改第一处。
    多处匹配时静默替换是最难排查的 agent 故障——模型以为改对了继续往下跑，
    错误在几十轮后才暴露。需要全改时显式传 replace_all=true。
    """
    filepath = arguments.get("filepath", "") or arguments.get("path", "")
    old_text = arguments.get("old_text", "")
    new_text = arguments.get("new_text", "")
    replace_all = bool(arguments.get("replace_all") or False)

    if not filepath or old_text == "":
        return "[Error] filepath and old_text are required"

    if old_text == new_text:
        return "[Error] old_text and new_text are identical; nothing to change"

    try:
        from backend.agent.progress_guard import is_diag_junk_path

        if is_diag_junk_path(str(filepath)):
            return (
                f"[Blocked] 禁止编辑诊断垃圾：{filepath}。"
                "请改 crates/... 业务源码。"
            )
    except Exception:
        pass

    base_path = config.get("base_path", "./workspace")
    full_path, base_abs = _resolve_workspace_path(base_path, filepath)

    # 路径安全检查（按路径分量比较 + resolve 符号链接，见 _is_within）
    if not _is_within(full_path, base_abs):
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

        occurrences = content.count(old_text)

        if occurrences == 0:
            return (
                f"[Error] old_text not found in {filepath}. "
                f"Read the file first and copy the exact text, including indentation "
                f"and line breaks."
            )

        if occurrences > 1 and not replace_all:
            # 定位前两处所在行号，帮模型判断该扩多少上下文
            first = content[: content.index(old_text)].count("\n") + 1
            second_off = content.index(old_text, content.index(old_text) + 1)
            second = content[:second_off].count("\n") + 1
            return (
                f"[Error] old_text appears {occurrences} times in {filepath} "
                f"(first at line {first}, next at line {second}). "
                f"Include more surrounding lines to make it unique, "
                f"or pass replace_all=true to replace every occurrence."
            )

        line_no = content[: content.index(old_text)].count("\n") + 1
        if replace_all:
            new_content = content.replace(old_text, new_text)
        else:
            new_content = content.replace(old_text, new_text, 1)

        with open(full_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        if replace_all and occurrences > 1:
            return (
                f"[Success] Edited {filepath}: replaced all {occurrences} occurrences "
                f"(first at line {line_no}), {len(old_text)} -> {len(new_text)} chars each"
            )
        return (
            f"[Success] Edited {filepath}:{line_no} — "
            f"replaced {len(old_text)} chars with {len(new_text)} chars"
        )
    except Exception as e:
        return f"[Error] {e}"


# glob/grep 默认跳过的目录（防 node_modules 把上下文与预算打穿）
_GLOB_SKIP_DIR_NAMES = frozenset({
    "node_modules",
    ".git",
    ".hg",
    ".svn",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "coverage",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".computers",
    "win-python",
    ".cache",
})
_GLOB_MAX_FILES = 80
_GLOB_MAX_CHARS = 12_000
_GREP_MAX_MATCHES = 80
_GREP_MAX_CHARS = 12_000
_GREP_MAX_FILES_SCAN = 2_000


def _path_has_skipped_segment(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    return any(p in _GLOB_SKIP_DIR_NAMES for p in parts)


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
    include_heavy = bool(arguments.get("include_heavy") or arguments.get("all"))
    # Hard cap: recursive ** globs over huge trees (node_modules etc. already
    # filtered, but scan itself can block 20s+ and stress the process before
    # Codex SSE). Default 15s; env TEVARN_GLOB_TIMEOUT_SEC overrides.
    try:
        _glob_timeout = float(os.environ.get("TEVARN_GLOB_TIMEOUT_SEC") or "15")
    except Exception:
        _glob_timeout = 15.0
    _glob_timeout = max(3.0, min(_glob_timeout, 60.0))

    def _scan() -> str:
        matches = glob.glob(search_path, recursive=True)
        rel_matches: list[str] = []
        skipped_heavy = 0
        for m in sorted(matches):
            m_abs = os.path.abspath(m)
            if not _is_within(m_abs, base_abs):
                continue
            if not os.path.isfile(m):
                continue
            rel = os.path.relpath(m, base_abs)
            if not include_heavy and _path_has_skipped_segment(rel):
                skipped_heavy += 1
                continue
            rel_matches.append(rel)

        total = len(rel_matches)
        if total == 0:
            if skipped_heavy:
                return (
                    f"No files matched (excluded {skipped_heavy} under "
                    f"node_modules/.git/dist/…; pass include_heavy=true to force)."
                )
            return "No files matched."

        shown = rel_matches[:_GLOB_MAX_FILES]
        body = "\n".join(shown)
        truncated_files = total > _GLOB_MAX_FILES
        if len(body) > _GLOB_MAX_CHARS:
            body = body[: _GLOB_MAX_CHARS - 1] + "…"
            truncated_files = True

        header = f"Matched {total} file(s)"
        if truncated_files:
            header += f" (showing ≤{_GLOB_MAX_FILES} paths / ≤{_GLOB_MAX_CHARS} chars — narrow pattern)"
        if skipped_heavy:
            header += f"; skipped {skipped_heavy} heavy-dir paths"
        header += ":\n"
        return header + body

    try:
        # 阻塞的目录遍历丢进线程，同轮并发的 tool call 才能真正重叠（T1b）
        # 超时保护：避免 ** 扫盘拖死进程后再打 Codex SSE
        return await asyncio.wait_for(asyncio.to_thread(_scan), timeout=_glob_timeout)
    except asyncio.TimeoutError:
        return (
            f"[Error] glob timed out after {_glob_timeout:.0f}s for pattern={pattern!r}. "
            "Narrow the pattern (avoid bare **/… over huge trees) or set include_heavy carefully."
        )
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

    # 路径安全检查（按路径分量比较 + resolve 符号链接，见 _is_within）
    if not _is_within(target_path, base_abs):
        return (
            f"[Security Blocked] Path '{path}' is outside the allowed directory"
        )

    if not os.path.exists(target_path):
        return f"[Error] Path not found: {path}"

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"[Error] Invalid regex pattern: {e}"

    include_heavy = bool(arguments.get("include_heavy") or arguments.get("all"))

    def _scan() -> str:
        matches: list[str] = []
        files_scanned = 0
        skipped_heavy = 0

        if os.path.isfile(target_path):
            files = [target_path]
        elif os.path.isdir(target_path) and recursive:
            files = []
            for root, dirnames, filenames in os.walk(target_path):
                if not include_heavy:
                    dirnames[:] = [
                        d for d in dirnames if d not in _GLOB_SKIP_DIR_NAMES
                    ]
                for filename in filenames:
                    files.append(os.path.join(root, filename))
                    if len(files) >= _GREP_MAX_FILES_SCAN:
                        break
                if len(files) >= _GREP_MAX_FILES_SCAN:
                    break
        else:
            return f"[Error] {path} is not a file or directory"

        for filepath in files:
            rel_path = os.path.relpath(filepath, base_abs)
            if not include_heavy and _path_has_skipped_segment(rel_path):
                skipped_heavy += 1
                continue
            files_scanned += 1
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if regex.search(line):
                            matches.append(f"{rel_path}:{i}: {line.rstrip()[:240]}")
                            if len(matches) >= _GREP_MAX_MATCHES:
                                break
                if len(matches) >= _GREP_MAX_MATCHES:
                    break
            except (UnicodeDecodeError, IsADirectoryError, PermissionError, OSError):
                continue

        if not matches:
            extra = f" (scanned {files_scanned} files"
            if skipped_heavy:
                extra += f", skipped {skipped_heavy} heavy-dir"
            extra += ")"
            return f"No matches found.{extra}"

        body = "\n".join(matches)
        truncated = len(matches) >= _GREP_MAX_MATCHES
        if len(body) > _GREP_MAX_CHARS:
            body = body[: _GREP_MAX_CHARS - 1] + "…"
            truncated = True
        header = f"Found {len(matches)} match(es)"
        if truncated:
            header += f" (cap {_GREP_MAX_MATCHES} lines / {_GREP_MAX_CHARS} chars — narrow path/pattern)"
        header += f"; scanned ≤{files_scanned} files"
        if skipped_heavy:
            header += f"; skipped {skipped_heavy} heavy-dir"
        header += ":\n"
        return header + body

    try:
        # 阻塞的 os.walk + 逐文件读丢进线程（T1b）
        return await asyncio.to_thread(_scan)
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

    # 路径安全检查（按路径分量比较 + resolve 符号链接，见 _is_within）
    if not _is_within(db_path, base_abs):
        return (
            f"[Security Blocked] Database path '{database}' "
            f"is outside the allowed directory"
        )

    def _run_query() -> str:
        # with 保证异常路径也关连接（此前 cursor.execute 抛错就泄漏一个连接）
        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query)

            upper = query.split(None, 1)[0].upper()
            if upper in ("SELECT", "PRAGMA", "WITH", "EXPLAIN"):
                rows = cursor.fetchall()
                if not rows:
                    return "Query executed successfully. No rows returned."

                headers = rows[0].keys()
                lines = [" | ".join(headers)]
                lines.append("-" * len(lines[0]))
                for row in rows[:50]:
                    lines.append(" | ".join(str(v) if v is not None else "NULL" for v in row))
                if len(rows) > 50:
                    lines.append(f"... ({len(rows) - 50} more rows)")
                return "\n".join(lines)

            conn.commit()
            return f"Query executed successfully. Rows affected: {cursor.rowcount}"

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
