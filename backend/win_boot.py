"""Windows-safe uvicorn entrypoint (always shipped with backend package).

Electron / packagers should launch::

    python -m backend.win_boot --host 127.0.0.1 --port 8090

rather than ``python -m uvicorn backend.main:app`` so that:

1. ``WindowsSelectorEventLoopPolicy`` is set **before** uvicorn creates a loop
2. crash forensics env (faulthandler / unbuffered IO / codex isolate) is armed
3. a process breadcrumb is written on boot for silent-death forensics

On non-Windows this is a thin uvicorn wrapper (policy no-op).
"""
from __future__ import annotations

from backend.core.config import get_tevarn_home

import argparse
import asyncio
import os
import sys
from pathlib import Path


def _arm_env() -> None:
    os.environ.setdefault("PYTHONFAULTHANDLER", "1")
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    os.environ.setdefault("PYTHONUTF8", "1")
    if sys.platform == "win32":
        # Child dies, parent lives — default ON for Windows Codex SSE.
        os.environ.setdefault("TEVARN_CODEX_SSE_ISOLATE", "1")


def selector_loop_factory(*_args: object, **_kwargs: object) -> asyncio.AbstractEventLoop:
    """Uvicorn custom loop factory — must return a **loop instance**.

    Uvicorn 0.50 on Windows defaults to Proactor via asyncio_loop_factory::

        if sys.platform == "win32" and not use_subprocess:
            return asyncio.ProactorEventLoop  # class → silent hard-exit class

    Custom ``loop="backend.win_boot:selector_loop_factory"`` is imported as the
    zero-arg factory passed to ``asyncio.Runner``; it must return an *instance*,
    not a class (class would break create_task unbound).
    """
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


def _arm_selector_policy() -> None:
    """Force Selector policy early (belt); real loop comes from selector_loop_factory."""
    if sys.platform != "win32":
        return
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass


def _arm_faulthandler() -> Path | None:
    try:
        import faulthandler

        # Prefer resources/logs next to package; fall back to ~/.tevarn/logs
        candidates = [
            Path(__file__).resolve().parents[1] / "logs",
            Path(os.environ.get("TEVARN_HOME") or (get_tevarn_home())) / "logs",
        ]
        fh_dir = candidates[0]
        try:
            fh_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            fh_dir = candidates[1]
            fh_dir.mkdir(parents=True, exist_ok=True)
        fh_path = fh_dir / "faulthandler.log"
        # Unbuffered binary so hard-kill still has a chance of partial dump
        fp = open(fh_path, "ab", buffering=0)  # noqa: SIM115 — process-lifetime
        faulthandler.enable(file=fp, all_threads=True)
        return fh_path
    except Exception:
        try:
            import faulthandler

            faulthandler.enable(all_threads=True)
        except Exception:
            pass
        return None


def _breadcrumb(event: str, **extra: object) -> None:
    try:
        from backend.core.process_guard import write_breadcrumb

        write_breadcrumb(event, **extra)
    except Exception:
        pass


def main(argv: list[str] | None = None) -> None:
    _arm_env()
    _arm_selector_policy()
    fh = _arm_faulthandler()

    parser = argparse.ArgumentParser(prog="backend.win_boot")
    parser.add_argument("--host", default=os.environ.get("TEVARN_APP_HOST") or "127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("TEVARN_APP_PORT") or os.environ.get("TEVARN_PORT") or "8090"),
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("TEVARN_LOG_LEVEL") or "info",
    )
    args = parser.parse_args(argv)

    _breadcrumb(
        "win_boot.start",
        host=args.host,
        port=args.port,
        faulthandler=str(fh) if fh else "",
        policy="selector" if sys.platform == "win32" else "default",
        python=sys.version.split()[0],
    )

    import uvicorn

    # Critical: use our factory so the *running* loop is SelectorEventLoop.
    # Policy alone is insufficient — uvicorn 0.50 returns ProactorEventLoop class
    # from asyncio_loop_factory on win32 by default.
    uvicorn.run(
        "backend.main:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        reload=False,
        timeout_keep_alive=75,
        loop="backend.win_boot:selector_loop_factory",
        http="h11",  # avoid httptools edge cases on win32 multi-stream
    )


if __name__ == "__main__":
    main()
