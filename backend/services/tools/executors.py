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
from typing import Any

logger = logging.getLogger(__name__)


def _resolve_workspace_path(base_path: str, filepath: str) -> tuple[str, str]:
    """解析 workspace 内的安全路径，去掉 filepath 中重复的 workspace 前缀。
    
    Returns:
        (full_path, base_abs) 元组
    """
    _fp = filepath.replace("\\", "/").lstrip("/")
    # 用 basename 匹配前缀，兼容绝对路径 base_path（如 E:\项目\takton\workspace）
    _basename = os.path.basename(base_path.rstrip("/\\").replace("\\", "/"))
    _bp_rel = base_path.replace("\\", "/").rstrip("/").lstrip("./")
    # 同时检查 basename 和相对路径两种前缀
    for prefix in {_basename, _bp_rel}:
        if prefix and _fp.startswith(prefix + "/"):
            _fp = _fp[len(prefix) + 1:]
            break
    full_path = os.path.abspath(os.path.join(base_path, _fp))
    base_abs = os.path.abspath(base_path)
    return full_path, base_abs


# 安全命令白名单（仅允许这些命令名，不是前缀匹配）
_SAFE_COMMANDS: set[str] = {
    "ls", "cat", "head", "tail", "grep", "find", "pwd", "echo",
    "ps", "df", "du", "whoami", "uname", "date", "wc", "sort",
    "mkdir", "touch", "cp", "mv", "rm", "rmdir",
}

# 完全禁止的子串（包括换行、反引号等）
_DANGEROUS_CHARS = [";", "&&", "||", "|", "`", "$()", ">>", "<(", ">/dev/null", "\n", "\r", "&", "$(", "${"]


async def execute_browser(config: dict[str, Any], arguments: dict[str, Any]) -> str:
    """
    浏览器工具：获取网页内容
    使用 aiohttp 获取页面 HTML（简单模式，不执行 JS）
    """
    url = arguments.get("url", "")
    if not url:
        return "[Error] URL is required"

    timeout = config.get("timeout", 30)
    user_agent = config.get(
        "user_agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
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


async def execute_command(config: dict[str, Any], arguments: dict[str, Any]) -> str:
    """
    命令行工具：执行 shell 命令（安全模式）

    修复说明（v2）：
    1. 使用 shlex.split 将命令解析为参数列表
    2. 只允许单个命令（禁止管道、逻辑运算符、命令分隔符）
    3. 命令名必须在白名单中
    4. 使用 create_subprocess_exec 而非 shell，避免 shell 注入
    """
    command = arguments.get("command", "").strip()
    if not command:
        return "[Error] command is required"

    safe_mode = config.get("safe_mode", True)
    if safe_mode:
        # 检查是否包含危险字符
        if any(d in command for d in _DANGEROUS_CHARS):
            return (
                f"[Security Blocked] Dangerous characters detected in: {command}. "
                f"Pipes, logic operators, and command separators are not allowed."
            )

        # 使用 shlex.split 解析命令
        try:
            args = shlex.split(command)
        except ValueError as e:
            return f"[Security Blocked] Invalid command syntax: {e}"

        if not args:
            return "[Error] Empty command"

        # 只允许单个命令（禁止管道等），shlex.split 后 args 列表代表单个命令
        cmd_name = os.path.basename(args[0])

        if cmd_name not in _SAFE_COMMANDS:
            return (
                f"[Security Blocked] Command '{cmd_name}' is not in the safe whitelist. "
                f"Allowed: {', '.join(sorted(_SAFE_COMMANDS))}"
            )

        # 禁止路径中包含 .. 以防止目录遍历
        for arg in args:
            if ".." in arg:
                return (
                    f"[Security Blocked] Path traversal detected in argument: {arg}"
                )
    else:
        # safe_mode = False 时仍需解析，但不做白名单限制
        try:
            args = shlex.split(command)
        except ValueError as e:
            return f"[Error] Invalid command syntax: {e}"

    timeout = arguments.get("timeout", config.get("timeout", 30))
    working_dir = config.get("working_dir")

    try:
        # 使用 exec 而非 shell，直接传递参数列表，彻底避免 shell 注入
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir if working_dir and os.path.isdir(working_dir) else None,
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
        # 强制终止超时进程
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return f"[Timeout] Command exceeded {timeout}s and was terminated"
    except FileNotFoundError:
        return f"[Error] Command not found: {args[0] if args else command}"
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
    if not full_path.startswith(base_abs):
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
    if not full_path.startswith(base_abs):
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

    # 安全：禁止 import 某些模块
    banned_imports = ["os.system", "subprocess", "socket", "urllib.request.urlopen"]
    for banned in banned_imports:
        if banned in code:
            return f"[Security Blocked] Usage of '{banned}' is not allowed"

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
    网络搜索工具：搜索网页信息
    优先 DuckDuckGo，失败时自动 fallback 到 Bing
    无需 API Key
    """
    query = arguments.get("query", "").strip()
    if not query:
        return "[Error] query is required"

    max_results = arguments.get("max_results", config.get("max_results", 5))
    engine = config.get("engine", "auto")  # auto, duckduckgo, bing

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        import aiohttp
    except ImportError:
        return "[Error] aiohttp is not installed"

    errors = []

    # 尝试 DuckDuckGo
    if engine in ("auto", "duckduckgo"):
        try:
            results = await _search_duckduckgo(query, max_results, headers)
            if results:
                return "\n\n".join(results)
        except Exception as e:
            errors.append(f"DuckDuckGo: {e}")

    # Fallback 到 Bing
    if engine in ("auto", "bing"):
        try:
            results = await _search_bing(query, max_results, headers)
            if results:
                return "\n\n".join(results)
        except Exception as e:
            errors.append(f"Bing: {e}")

    if errors:
        return f"[Error] Search failed. Details: {'; '.join(errors)}"
    return "No results found."


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
}
