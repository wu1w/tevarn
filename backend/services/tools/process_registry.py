"""后台进程注册表（command background=true）— 轻量对标 Hermes process。

Supports proactive completion injection: when a bg job finishes, enqueue a
one-shot notice per process id for the owning session (drained before next LLM).
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


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
    session_id: str | None = None
    completion_notified: bool = False
    # Poll thrash control (while still running)
    last_poll_at: float = 0.0
    poll_count_running: int = 0
    last_poll_out_len: int = 0
    last_poll_err_len: int = 0
    _task: asyncio.Task | None = field(default=None, repr=False)


_REGISTRY: dict[str, BgProcess] = {}
_LOCK = asyncio.Lock()
_MAX = 32
# audit-fix: communicate 输出截断预算（保留尾部 64KB）
_MAX_OUTPUT_BYTES = 64 * 1024
# session_id -> ordered process ids pending inject (once each)
_PENDING_BY_SESSION: dict[str, list[str]] = {}


def _mark_done_and_notify(item: BgProcess) -> None:
    """Set done and enqueue one-shot completion for the agent loop."""
    item.done = True
    try:
        _enqueue_completion(item)
    except Exception as e:
        logger.debug("bg completion enqueue skip: %s", e)


def _enqueue_completion(item: BgProcess) -> None:
    if item.completion_notified:
        return
    sid = (item.session_id or "").strip()
    if not sid:
        return
    item.completion_notified = True
    q = _PENDING_BY_SESSION.setdefault(sid, [])
    if item.id not in q:
        q.append(item.id)
        logger.info(
            "bg complete queued session=%s id=%s exit=%s cmd=%s",
            sid[:8],
            item.id,
            item.exit_code,
            (item.command or "")[:80],
        )


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


def poll_process_throttled(p: BgProcess, *, tail: int = 8000) -> str:
    """Format process with hard poll throttle while still running.

    - Min interval between polls when no new output
    - Max polls while running (then block further empty polls)
    Flags: agent_process_poll_block_enabled / min_interval_s / max_while_running
    """
    try:
        from backend.core.config import settings as _st

        enabled = bool(getattr(_st, "agent_process_poll_block_enabled", True))
        min_iv = float(getattr(_st, "agent_process_poll_min_interval_s", 8) or 8)
        max_n = int(getattr(_st, "agent_process_poll_max_while_running", 3) or 3)
    except Exception:
        enabled, min_iv, max_n = True, 8.0, 3

    if p.done or not enabled:
        # reset running counters once done so later log tail is fine
        if p.done:
            p.poll_count_running = 0
        return format_process(p, tail=tail)

    now = time.time()
    out_len = len(p.stdout or "")
    err_len = len(p.stderr or "")
    new_output = out_len != p.last_poll_out_len or err_len != p.last_poll_err_len

    if p.poll_count_running >= max(1, max_n) and not new_output:
        return (
            f"[Blocked] process poll 上限（仍在运行，已 poll {p.poll_count_running} 次无新输出）。\n"
            f"[bg {p.id}] status=running exit=None\n"
            f"command: {p.command}\n"
            "NEXT:\n"
            "1) 等系统 [bg_complete] 自动注入结果（勿再 poll 同一 id）\n"
            "2) file_write/edit 业务源码推进任务\n"
            "3) process kill 仅当确认卡死\n"
        )

    if (
        p.last_poll_at > 0
        and not new_output
        and (now - p.last_poll_at) < max(1.0, min_iv)
    ):
        wait_left = max(1, int(max(1.0, min_iv) - (now - p.last_poll_at)))
        return (
            f"[Poll throttle] Still running with no new output; wait ≥{int(min_iv)}s "
            f"(~{wait_left}s) or wait for [bg_complete] auto-inject.\n"
            f"[bg {p.id}] status=running exit=None\n"
            f"command: {p.command}\n"
            "NEXT:\n"
            "1) Real work (edit sources / manage_goal), not spam poll\n"
            "2) Wait for bg_complete\n"
            "3) Avoid Still-running / wait-more thrash\n"
        )

    p.poll_count_running = int(p.poll_count_running or 0) + 1
    p.last_poll_at = now
    p.last_poll_out_len = out_len
    p.last_poll_err_len = err_len
    body = format_process(p, tail=tail)
    if not p.done:
        body += (
            "\n(still running: [bg_complete] injects when done; "
            f"do not spam empty poll, interval ≥{int(min_iv)}s. "
            f"polls while running for this id: {p.poll_count_running}/{max_n})"
        )
    return body


def drain_session_completions(
    session_id: str,
    *,
    max_n: int = 8,
    tail: int = 8000,
) -> list[str]:
    """Return inject texts for finished bg jobs (once per process id).

    Each entry is:
      [bg_complete process_id=… source=auto]
      + format_process(p)   # same shape as process poll
    """
    sid = str(session_id or "").strip()
    if not sid:
        return []
    pids = _PENDING_BY_SESSION.pop(sid, [])
    if not pids:
        return []
    out: list[str] = []
    for pid in pids[: max(1, max_n)]:
        p = _REGISTRY.get(pid)
        if p is None or not p.done:
            continue
        body = format_process(p, tail=tail)
        out.append(
            f"[bg_complete process_id={p.id} source=auto]\n"
            f"{body}\n"
            "（后台任务已结束，系统自动注入；无需再 process poll 同一 id，"
            "除非你需要更长日志尾。若 cargo 失败请 file_write/edit 修源码。）"
        )
    rest = pids[max(1, max_n) :]
    if rest:
        _PENDING_BY_SESSION[sid] = rest + _PENDING_BY_SESSION.get(sid, [])
    return out


def peek_pending_count(session_id: str) -> int:
    return len(_PENDING_BY_SESSION.get(str(session_id or "").strip(), []) or [])


async def adopt_running(
    proc: Any,
    command: str,
    *,
    cwd: str | None = None,
    partial_stdout: bytes | str = b"",
    partial_stderr: bytes | str = b"",
    session_id: str | None = None,
) -> BgProcess:
    """Register an already-spawned process as background (timeout→bg)."""
    async with _LOCK:
        if len(_REGISTRY) >= _MAX:
            for k in list(_REGISTRY.keys()):
                if _REGISTRY[k].done:
                    _REGISTRY.pop(k, None)
        if len(_REGISTRY) >= _MAX:
            raise RuntimeError(
                f"background process registry full ({_MAX} still running)"
            )
        pid = f"bg_{uuid.uuid4().hex[:10]}"
        item = BgProcess(
            id=pid,
            command=command,
            cwd=cwd,
            started_at=time.time(),
            session_id=str(session_id).strip() if session_id else None,
        )
        item.proc = proc
        if isinstance(partial_stdout, bytes):
            item.stdout = partial_stdout.decode("utf-8", errors="replace")
        else:
            item.stdout = str(partial_stdout or "")
        if isinstance(partial_stderr, bytes):
            item.stderr = partial_stderr.decode("utf-8", errors="replace")
        else:
            item.stderr = str(partial_stderr or "")
        _REGISTRY[pid] = item

    async def _drain() -> None:
        try:
            if proc is None:
                item.exit_code = -1
                return
            out, err = await proc.communicate()
            if out and len(out) > _MAX_OUTPUT_BYTES:
                out = out[-_MAX_OUTPUT_BYTES:]
            if err and len(err) > _MAX_OUTPUT_BYTES:
                err = err[-_MAX_OUTPUT_BYTES:]
            if out:
                item.stdout = (item.stdout or "") + out.decode(
                    "utf-8", errors="replace"
                )
            if err:
                item.stderr = (item.stderr or "") + err.decode(
                    "utf-8", errors="replace"
                )
            item.exit_code = proc.returncode
        except Exception as e:
            item.error = str(e)
            item.exit_code = -1
        finally:
            _mark_done_and_notify(item)

    item._task = asyncio.create_task(_drain())
    return item


async def start_background(
    command: str,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    session_id: str | None = None,
) -> BgProcess:
    async with _LOCK:
        if len(_REGISTRY) >= _MAX:
            for k in list(_REGISTRY.keys()):
                if _REGISTRY[k].done:
                    _REGISTRY.pop(k, None)
        if len(_REGISTRY) >= _MAX:
            raise RuntimeError(
                f"background process registry full ({_MAX} still running); "
                "poll or stop existing bg processes before starting new ones"
            )
        pid = f"bg_{uuid.uuid4().hex[:10]}"
        item = BgProcess(
            id=pid,
            command=command,
            cwd=cwd,
            started_at=time.time(),
            session_id=str(session_id).strip() if session_id else None,
        )
        _REGISTRY[pid] = item

    async def _run() -> None:
        try:
            from backend.core.safe_subprocess import create_process

            run_cmd = command
            run_env = dict(env) if env else None
            try:
                import os as _os
                from backend.core.msvc_env import (
                    apply_host_rust_env,
                    merge_msvc_env,
                    needs_msvc_toolchain,
                    prepend_vcvars_call,
                )

                if needs_msvc_toolchain(command):
                    from backend.core.msvc_env import rewrite_cargo_to_absolute

                    base = dict(_os.environ)
                    if run_env:
                        base.update(run_env)
                    host = (
                        base.get("USERPROFILE")
                        or _os.environ.get("USERPROFILE")
                        or ""
                    )
                    run_env = apply_host_rust_env(base, host)
                    run_env = merge_msvc_env(run_env)
                    run_cmd = rewrite_cargo_to_absolute(command, host)
                    run_cmd = prepend_vcvars_call(run_cmd)
            except Exception:
                pass

            proc = await create_process(
                run_cmd,
                cwd=cwd if cwd else None,
                env=run_env,
            )
            item.proc = proc
            out, err = await proc.communicate()
            if out and len(out) > _MAX_OUTPUT_BYTES:
                out = out[-_MAX_OUTPUT_BYTES:]
            if err and len(err) > _MAX_OUTPUT_BYTES:
                err = err[-_MAX_OUTPUT_BYTES:]
            item.stdout = out.decode("utf-8", errors="replace")
            item.stderr = err.decode("utf-8", errors="replace")
            item.exit_code = proc.returncode
        except Exception as e:
            item.error = str(e)
            item.exit_code = -1
        finally:
            _mark_done_and_notify(item)

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
                "session_id": p.session_id,
            }
        )
    return out
