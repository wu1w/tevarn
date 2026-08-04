"""Windows-safe uvicorn entry: SelectorEventLoop before the loop is created.

Proactor + concurrent aiohttp Codex SSE has caused silent process exits on
Python 3.14 / Windows. Import this module (or run as __main__) so policy is
set before uvicorn boots the loop.
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

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass


def main() -> None:
    import uvicorn

    host = os.environ.get("TAKTON_HOST", "127.0.0.1")
    port = int(os.environ.get("TAKTON_PORT", "8090"))
    uvicorn.run(
        "backend.main:app",
        host=host,
        port=port,
        log_level=os.environ.get("TAKTON_LOG_LEVEL", "info"),
        # reload off: multi-process reload fights our crash diagnostics
        reload=False,
    )


if __name__ == "__main__":
    main()
