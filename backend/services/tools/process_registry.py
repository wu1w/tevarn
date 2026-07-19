"""后台进程注册表（command background=true）— 轻量对标 Hermes process。"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BgProcess:
    id: str
    command: str
    cwd: str | None
    started_at: float
    proc: Any = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    done: bool = False
    error: str | None = None
    _task: asyncio.Task | None = field(default=None, repr=False)


_REGISTRY: dict[str, BgProcess] = {}
_LOCK = asyncio.Lock()
_MAX = 32


async def start_background(
    command: str,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> BgProcess:
    async with _LOCK:
        # prune finished if full
        if len(_REGISTRY) >= _MAX:
            for k in list(_REGISTRY.keys()):
                if _REGISTRY[k].done:
                    _REGISTRY.pop(k, None)
        pid = f"bg_{uuid.uuid4().hex[:10]}"
        item = BgProcess(id=pid, command=command, cwd=cwd, started_at=time.time())
        _REGISTRY[pid] = item

    async def _run() -> None:
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd if cwd else None,
                env=env,
            )
            item.proc = proc
            out, err = await proc.communicate()
            item.stdout = out.decode("utf-8", errors="replace")
            item.stderr = err.decode("utf-8", errors="replace")
            item.exit_code = proc.returncode
        except Exception as e:
            item.error = str(e)
            item.exit_code = -1
        finally:
            item.done = True

    item._task = asyncio.create_task(_run())
    return item


def get_process(process_id: str) -> BgProcess | None:
    return _REGISTRY.get(process_id)


def list_processes() -> list[dict[str, Any]]:
    out = []
    for p in _REGISTRY.values():
        out.append(
            {
                "id": p.id,
                "command": p.command[:200],
                "cwd": p.cwd,
                "done": p.done,
                "exit_code": p.exit_code,
                "started_at": p.started_at,
                "stdout_len": len(p.stdout),
                "stderr_len": len(p.stderr),
                "error": p.error,
            }
        )
    return out


def format_process(p: BgProcess, *, tail: int = 8000) -> str:
    status = "done" if p.done else "running"
    lines = [
        f"[bg {p.id}] status={status} exit={p.exit_code}",
        f"command: {p.command}",
    ]
    if p.cwd:
        lines.append(f"cwd: {p.cwd}")
    if p.error:
        lines.append(f"error: {p.error}")
    out = p.stdout[-tail:] if p.stdout else ""
    err = p.stderr[-tail:] if p.stderr else ""
    if out:
        lines.append("--- stdout ---")
        lines.append(out)
    if err:
        lines.append("--- stderr ---")
        lines.append(err)
    if not p.done and not out and not err:
        lines.append("(still running, no output yet)")
    return "\n".join(lines)
