from __future__ import annotations

import asyncio
import os
import re
import shlex
from typing import Any


# 危险模式：拦截明显灾难命令（MVP 黑名单，非完备沙箱）
_BLOCK = re.compile(
    r"""(?ix)
    (rm\s+-rf\s+[/\\]|
     del\s+/[fq]\s+|
     format\s+|
     mkfs\.|
     :\(\)\s*\{|
     shutdown|
     reboot|
     diskpart|
     Remove-Item\s+.*-Recurse|
     reg\s+delete)
    """
)


class ExecService:
    def __init__(self, root: str, timeout_s: float = 30.0):
        self.root = root
        self.timeout_s = timeout_s

    async def run(self, command: str, cwd: str | None = None) -> dict[str, Any]:
        cmd = (command or "").strip()
        if not cmd:
            raise ValueError("empty command")
        if len(cmd) > 4000:
            raise ValueError("command too long")
        if _BLOCK.search(cmd):
            raise PermissionError("command blocked by policy")

        workdir = cwd or self.root
        if not os.path.isdir(workdir):
            workdir = self.root

        # 统一走 shell，支持管道/&&（仍有黑名单 + 超时）
        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_s
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            raise TimeoutError(f"exec timed out after {self.timeout_s}s")

        def dec(b: bytes) -> str:
            return b.decode("utf-8", errors="replace")

        out = dec(stdout)
        err = dec(stderr)
        # cap output
        max_len = 32_000
        if len(out) > max_len:
            out = out[:max_len] + "\n…[truncated]"
        if len(err) > max_len:
            err = err[:max_len] + "\n…[truncated]"

        return {
            "command": cmd,
            "cwd": workdir,
            "exit_code": proc.returncode,
            "stdout": out,
            "stderr": err,
        }
