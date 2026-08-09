"""Windows-safe uvicorn entry: SelectorEventLoop before the loop is created.

Proactor + concurrent aiohttp Codex SSE has caused silent process exits on
Python 3.14 / Windows. Import this module (or run as __main__) so policy is
set before uvicorn boots the loop.

Also enables faulthandler + unbuffered IO for crash forensics.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Repo root on PYTHONPATH
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Crash forensics / log flush
os.environ.setdefault("PYTHONFAULTHANDLER", "1")
os.environ.setdefault("PYTHONUNBUFFERED", "1")
os.environ.setdefault("PYTHONUTF8", "1")
# Codex SSE isolation ON by default on Windows (child dies, parent lives)
if sys.platform == "win32":
    os.environ.setdefault("TEVARN_CODEX_SSE_ISOLATE", "1")

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

try:
    import faulthandler

    _fh_dir = _ROOT / "logs"
    _fh_dir.mkdir(parents=True, exist_ok=True)
    _fh_path = _fh_dir / "faulthandler.log"
    _fh_fp = open(_fh_path, "a", encoding="utf-8")  # noqa: SIM115
    faulthandler.enable(file=_fh_fp, all_threads=True)
except Exception:
    try:
        import faulthandler

        faulthandler.enable()
    except Exception:
        pass


def main() -> None:
    # Prefer the always-shipped package entry (same behavior, one SSOT).
    try:
        from backend.win_boot import main as win_main

        host = os.environ.get("TEVARN_HOST") or os.environ.get("TEVARN_APP_HOST") or "127.0.0.1"
        port = os.environ.get("TEVARN_PORT") or os.environ.get("TEVARN_APP_PORT") or "8090"
        level = os.environ.get("TEVARN_LOG_LEVEL") or "info"
        win_main(["--host", str(host), "--port", str(port), "--log-level", str(level)])
        return
    except Exception:
        pass

    import uvicorn

    host = os.environ.get("TEVARN_HOST", "127.0.0.1")
    port = int(os.environ.get("TEVARN_PORT", "8090"))
    uvicorn.run(
        "backend.main:app",
        host=host,
        port=port,
        log_level=os.environ.get("TEVARN_LOG_LEVEL", "info"),
        # reload off: multi-process reload fights our crash diagnostics
        reload=False,
        # limit worker threads slightly; Codex isolate uses extra processes
        timeout_keep_alive=75,
    )


if __name__ == "__main__":
    main()
