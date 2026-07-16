"""
Bash Skill - 本地 Shell 命令执行
⚠️ 安全提醒：仅允许白名单内的只读/安全命令，且不经过 shell 解析（避免命令替换/管道注入）
"""

import re
import shlex

from ..base import BaseSkill

# 安全可执行文件白名单（严格匹配 argv[0]，不做前缀字符串匹配）
_SAFE_BINARIES = {
    "ls", "cat", "head", "tail", "grep", "find", "pwd", "echo",
    "ps", "df", "du", "whoami", "uname", "date", "wc", "sort", "git",
}

# git 仅允许只读子命令
_SAFE_GIT_SUBCOMMANDS = {"status", "log", "diff", "branch"}

# find 命令禁止使用的危险参数（可用于任意代码执行/文件删除）
_FIND_DANGEROUS_FLAGS = {
    "-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprintf", "-fls",
}

# 禁止出现的原始字符串（即便通过 shlex 拆分也要额外兜底检查一次原始输入）
_DANGEROUS_SUBSTRINGS = ("&", ";", "&&", "||", "|", "`", "$(", ">>", "<(", "\n", "\r")

# 安全修复：额外禁止的危险参数模式（前缀匹配，防止 --git-dir=/path 绕过）
_DANGEROUS_ARG_PATTERNS = ("-C", "--git-dir", "--work-tree", "-c", "--namespace")


class BashSkill(BaseSkill):
    """Shell 命令执行 Skill"""

    name = "bash"
    description = (
        "在本地环境中执行安全的 shell 命令。"
        "可用于查看文件列表、读取文件内容、检查系统状态等。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 shell 命令",
            },
            "timeout": {
                "type": "integer",
                "description": "超时时间（秒，默认 30）",
                "default": 30,
            },
        },
        "required": ["command"],
    }

    async def execute(self, command: str, timeout: int = 30, **kwargs) -> str:
        """执行 shell 命令（安全模式：不经过 shell 解析，严格 argv 白名单）"""
        # 兼容 Agent Loop 注入的 user_id 等元数据，忽略即可
        cmd = command.strip()

        if any(sub in cmd for sub in _DANGEROUS_SUBSTRINGS):
            return f"[Security Blocked] Dangerous characters/sequences detected in: {cmd}"

        try:
            args = shlex.split(cmd)
        except ValueError as e:
            return f"[Security Blocked] Failed to parse command: {e}"

        if not args:
            return "[Error] Empty command"

        binary = args[0]
        if binary not in _SAFE_BINARIES:
            return (
                f"[Security Blocked] Command '{binary}' is not in the safe whitelist. "
                f"Allowed: {', '.join(sorted(_SAFE_BINARIES))}"
            )

        if binary == "git":
            # 安全修复：禁止 git 的危险参数（可切换目录或指定其他仓库）
            # 使用前缀匹配防止 --git-dir=/path 这种绕过
            for arg in args:
                for prefix in _DANGEROUS_ARG_PATTERNS:
                    if arg == prefix or arg.startswith(f"{prefix}="):
                        return f"[Security Blocked] Dangerous git argument detected: {arg}"
            if len(args) < 2 or args[1] not in _SAFE_GIT_SUBCOMMANDS:
                return (
                    f"[Security Blocked] Only read-only git subcommands are allowed: "
                    f"{', '.join(sorted(_SAFE_GIT_SUBCOMMANDS))}"
                )

        if binary == "find":
            if any(a in _FIND_DANGEROUS_FLAGS for a in args[1:]):
                return "[Security Blocked] Dangerous 'find' flags (-exec/-delete/...) are not allowed"

        try:
            import asyncio
            proc = await asyncio.create_subprocess_exec(
                *args,
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
            return f"[Timeout] Command exceeded {timeout}s"
        except FileNotFoundError:
            return f"[Error] Executable not found: {binary}"
        except Exception as e:
            return f"[Error] {e}"
