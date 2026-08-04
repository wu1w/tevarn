"""Parent-side helper: run Codex SSE in an isolated child process.

If the child dies (native crash / abort), the parent only sees a broken pipe
and returns a structured error — the API process stays alive.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)


def isolate_enabled() -> bool:
    """Default ON for Windows; override with TAKTON_CODEX_SSE_ISOLATE=0/1."""
    raw = (os.environ.get("TAKTON_CODEX_SSE_ISOLATE") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    return sys.platform == "win32"


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
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONFAULTHANDLER"] = "1"
    # Avoid proxy PAC side effects confusing child; trust explicit env only
    cmd = [
        sys.executable,
        "-X",
        "faulthandler",
        "-u",
        "-m",
        "backend.services.llm.codex_sse_worker",
    ]
    logger.info(
        "codex SSE isolate spawn model=%s input_items=%s",
        payload.get("model"),
        len(payload.get("input") or []),
    )
    _flush_logs()

    # Optional: arm faulthandler dump if stream hangs forever in parent wait
    fh_armed = False
    try:
        import faulthandler

        faulthandler.dump_traceback_later(180, repeat=True)
        fh_armed = True
    except Exception:
        pass

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except Exception as e:
        logger.error("codex SSE isolate spawn failed: %s", e)
        if fh_armed:
            try:
                import faulthandler

                faulthandler.cancel_dump_traceback_later()
            except Exception:
                pass
        raise

    assert proc.stdin and proc.stdout
    try:
        proc.stdin.write(json.dumps(cfg, ensure_ascii=False).encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
    except Exception as e:
        logger.warning("codex SSE isolate stdin write failed: %s", e)
        try:
            proc.kill()
        except Exception:
            pass
        raise

    async def _drain_stderr() -> bytes:
        assert proc.stderr
        try:
            data = await proc.stderr.read()
            return data
        except Exception:
            return b""

    stderr_task = asyncio.create_task(_drain_stderr())
    try:
        assert proc.stdout
        while True:
            chunk = await proc.stdout.read(8192)
            if not chunk:
                break
            yield chunk
        rc = await proc.wait()
        err = b""
        try:
            err = await stderr_task
        except Exception:
            pass
        err_s = err.decode("utf-8", errors="replace")[-800:] if err else ""
        if rc != 0:
            logger.warning(
                "codex SSE isolate child exit=%s stderr=%s",
                rc,
                err_s[:400],
            )
            # If child wrote nothing usable, synthesize error SSE
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
                logger.info("codex SSE isolate child ok stderr_tail=%s", err_s[-200:])
    finally:
        if fh_armed:
            try:
                import faulthandler

                faulthandler.cancel_dump_traceback_later()
            except Exception:
                pass
        if proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except Exception:
                pass
        if not stderr_task.done():
            stderr_task.cancel()
        _flush_logs()


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
