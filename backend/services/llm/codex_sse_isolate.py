"""Parent-side helper: run Codex SSE in an isolated child process.

If the child dies (native crash / abort), the parent only sees a broken pipe
and returns a structured error — the API process stays alive.

Hardening (2026-08):
- process-level spawn lock (beyond asyncio sem) so thrashing can't overlap
- Windows CREATE_NEW_PROCESS_GROUP (+ optional BREAKAWAY) so the child is not
  glued into the parent's console/job in ways that cascade kills
- drop parent faulthandler.dump_traceback_later (races under multi-agent load)
- breadcrumb markers around spawn/exit for silent-death forensics
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Serialize isolate spawns across concurrent event-loop tasks (belt + suspenders
# with openai_codex_proxy._codex_sem).
_SPAWN_LOCK = threading.Lock()
_SPAWN_ASYNC_LOCK: asyncio.Lock | None = None
_SPAWN_ASYNC_LOCK_LOOP_ID: int | None = None


def isolate_enabled() -> bool:
    """Default ON for Windows; override with TEVARN_CODEX_SSE_ISOLATE=0/1."""
    raw = (os.environ.get("TEVARN_CODEX_SSE_ISOLATE") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return sys.platform == "win32"


def _async_spawn_lock() -> asyncio.Lock:
    """One asyncio.Lock per running loop (tests may recreate loops)."""
    global _SPAWN_ASYNC_LOCK, _SPAWN_ASYNC_LOCK_LOOP_ID
    try:
        loop = asyncio.get_running_loop()
        lid = id(loop)
    except RuntimeError:
        loop = None
        lid = None
    if _SPAWN_ASYNC_LOCK is None or lid != _SPAWN_ASYNC_LOCK_LOOP_ID:
        _SPAWN_ASYNC_LOCK = asyncio.Lock()
        _SPAWN_ASYNC_LOCK_LOOP_ID = lid
    return _SPAWN_ASYNC_LOCK


def _flush_logs() -> None:
    try:
        for h in logging.root.handlers:
            try:
                h.flush()
            except Exception:
                pass
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass


def _breadcrumb(event: str, **extra: Any) -> None:
    try:
        from backend.core.process_guard import write_breadcrumb

        write_breadcrumb(event, **extra)
    except Exception:
        pass


def _worker_script() -> Path:
    """Absolute path to codex_sse_worker.py (stdlib-only child entry).

    Prefer running the *file* rather than ``python -m backend...``: Windows
    embeddable Python uses pythonXX._pth which ignores PYTHONPATH, so a bare
    ``-m backend.services.llm.codex_sse_worker`` dies with ModuleNotFoundError
    and the agent loop sees empty LLM rounds → goal stall thrash.
    """
    return Path(__file__).resolve().parent / "codex_sse_worker.py"


def _child_env() -> dict[str, str]:
    """Env for the isolate child.

    Also prepend the package root (parent of ``backend/``) onto PYTHONPATH so
    any future worker imports of ``backend.*`` still resolve. Embeddable
    CPython may ignore PYTHONPATH for ``-m``; the file-path launch still
    benefits if the worker ever imports package code.
    """
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONFAULTHANDLER"] = "1"
    try:
        # backend/services/llm/this → package root is parents[2] (…/backend)
        # parent of package root must be on path for ``import backend``
        pkg_root = Path(__file__).resolve().parents[2]  # …/backend
        path_root = str(pkg_root.parent)  # …/resources or repo root
        cur = (env.get("PYTHONPATH") or "").strip()
        parts = [path_root]
        if cur:
            parts.append(cur)
        # also keep any sys.path entries that already host backend
        for p in sys.path:
            if not p or p == path_root:
                continue
            try:
                if (Path(p) / "backend").is_dir() and p not in parts:
                    parts.append(p)
            except Exception:
                pass
        env["PYTHONPATH"] = os.pathsep.join(parts)
    except Exception as e:
        logger.debug("codex isolate PYTHONPATH enrich skip: %s", e)
    return env


def _win_creationflags() -> int:
    """Flags that reduce cascade-kill risk with parent console/job.

    CREATE_BREAKAWAY_FROM_JOB only works if the parent job allows breakaway;
    if Assign fails at OS level, create_subprocess_exec raises — we fall back.
    """
    if sys.platform != "win32":
        return 0
    # win32 constants
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NO_WINDOW = 0x08000000
    CREATE_BREAKAWAY_FROM_JOB = 0x01000000
    flags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    # Breakaway often fails with WinError 5 when parent is not in a breakaway-ok
    # job — default OFF; set TEVARN_CODEX_SSE_BREAKAWAY=1 to try.
    raw = (os.environ.get("TEVARN_CODEX_SSE_BREAKAWAY") or "0").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        flags |= CREATE_BREAKAWAY_FROM_JOB
    return flags


async def iter_codex_sse_isolated(
    *,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_connect: float = 15.0,
    timeout_read: float = 300.0,
) -> AsyncIterator[bytes]:
    """Yield raw SSE bytes from a stdlib HTTP child process."""
    cfg = {
        "url": url,
        "headers": headers,
        "payload": payload,
        "timeout_connect": timeout_connect,
        "timeout_read": timeout_read,
    }
    env = _child_env()
    worker = _worker_script()
    if not worker.is_file():
        raise FileNotFoundError(f"codex_sse_worker missing: {worker}")
    # Run by file path — no ``import backend`` required at child start.
    cmd = [
        sys.executable,
        "-X",
        "faulthandler",
        "-u",
        str(worker),
    ]
    model = payload.get("model")
    n_items = len(payload.get("input") or [])
    logger.info(
        "codex SSE isolate spawn model=%s input_items=%s worker=%s",
        model,
        n_items,
        worker.name,
    )
    _flush_logs()
    _breadcrumb("codex_isolate_spawn", model=str(model or ""), input_items=n_items)

    async with _async_spawn_lock():
        # threading lock: defend against any re-entrant sync spawn paths
        with _SPAWN_LOCK:
            proc = await asyncio.to_thread(_spawn_worker_sync, cmd, env, worker)

        assert proc.stdin and proc.stdout

        def _write_stdin() -> None:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(cfg, ensure_ascii=False).encode("utf-8"))
            proc.stdin.close()

        try:
            await asyncio.to_thread(_write_stdin)
        except Exception as e:
            logger.warning("codex SSE isolate stdin write failed: %s", e)
            try:
                proc.kill()
            except Exception:
                pass
            _breadcrumb("codex_isolate_stdin_fail", err=str(e)[:160])
            raise

        stderr_buf: list[bytes] = []

        def _drain_stderr() -> None:
            try:
                assert proc.stderr is not None
                stderr_buf.append(proc.stderr.read() or b"")
            except Exception:
                stderr_buf.append(b"")

        stderr_thread = threading.Thread(target=_drain_stderr, name="codex-sse-err", daemon=True)
        stderr_thread.start()
        try:
            assert proc.stdout is not None
            while True:
                chunk = await asyncio.to_thread(proc.stdout.read, 8192)
                if not chunk:
                    break
                yield chunk
            rc = await asyncio.to_thread(proc.wait)
            stderr_thread.join(timeout=3)
            err = stderr_buf[0] if stderr_buf else b""
            err_s = err.decode("utf-8", errors="replace")[-800:] if err else ""
            if rc != 0:
                logger.warning(
                    "codex SSE isolate child exit=%s stderr=%s",
                    rc,
                    err_s[:400],
                )
                _breadcrumb("codex_isolate_child_fail", rc=rc, stderr=err_s[:200])
                msg = json.dumps(
                    {
                        "type": "error",
                        "status": 502,
                        "message": f"codex_sse_worker exit={rc} {err_s[:300]}",
                    },
                    ensure_ascii=False,
                )
                yield f"data: {msg}\n\n".encode("utf-8")
            else:
                if err_s:
                    logger.info(
                        "codex SSE isolate child ok stderr_tail=%s", err_s[-200:]
                    )
                _breadcrumb("codex_isolate_child_ok", rc=rc)
        finally:
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    await asyncio.to_thread(proc.wait)
                except Exception:
                    pass
            if stderr_thread.is_alive():
                stderr_thread.join(timeout=1)
            _flush_logs()


def _spawn_worker_sync(
    cmd: list[str], env: dict[str, str], worker: Path
) -> Any:
    """Sync Popen — works under SelectorEventLoop (asyncio subprocess does not on win32)."""
    import subprocess

    flags = _win_creationflags()
    kwargs: dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "env": env,
        "cwd": str(worker.parent),
    }
    if flags:
        kwargs["creationflags"] = flags
    try:
        return subprocess.Popen(cmd, **kwargs)  # noqa: S603 — fixed worker script
    except Exception as e:
        if flags and sys.platform == "win32":
            CREATE_BREAKAWAY_FROM_JOB = 0x01000000
            if flags & CREATE_BREAKAWAY_FROM_JOB:
                logger.warning(
                    "codex SSE isolate spawn with BREAKAWAY failed (%s); retry plain",
                    e,
                )
                kwargs["creationflags"] = flags & ~CREATE_BREAKAWAY_FROM_JOB
                try:
                    return subprocess.Popen(cmd, **kwargs)  # noqa: S603
                except Exception as e2:
                    logger.error("codex SSE isolate spawn failed: %s", e2)
                    _breadcrumb("codex_isolate_spawn_fail", err=str(e2)[:200])
                    raise
        logger.error("codex SSE isolate spawn failed: %s", e)
        _breadcrumb("codex_isolate_spawn_fail", err=str(e)[:200])
        raise


async def consume_sse_bytes_to_events(
    byte_iter: AsyncIterator[bytes],
) -> AsyncIterator[dict[str, Any] | str]:
    """Parse raw SSE stream into event dicts or the sentinel '[DONE]'."""
    buf = ""
    async for raw in byte_iter:
        if isinstance(raw, (bytes, bytearray)):
            text = raw.decode("utf-8", errors="replace")
        else:
            text = str(raw)
        buf += text
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            line = line.rstrip("\r")
            if not line:
                continue
            if line.startswith(":"):  # comment / keepalive
                continue
            if not line.startswith("data:"):
                continue
            data_s = line[5:].lstrip()
            if data_s == "[DONE]":
                yield "[DONE]"
                return
            try:
                ev = json.loads(data_s)
            except Exception:
                continue
            if isinstance(ev, dict):
                yield ev
    # flush trailing
    for line in buf.split("\n"):
        line = line.rstrip("\r")
        if line.startswith("data:"):
            data_s = line[5:].lstrip()
            if data_s == "[DONE]":
                yield "[DONE]"
                return
            try:
                ev = json.loads(data_s)
            except Exception:
                continue
            if isinstance(ev, dict):
                yield ev


__all__ = [
    "isolate_enabled",
    "iter_codex_sse_isolated",
    "consume_sse_bytes_to_events",
]
