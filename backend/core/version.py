"""产品版本号：权威源 backend/VERSION。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_FALLBACK = "0.4.10-alpha"


@lru_cache(maxsize=1)
def product_version() -> str:
    try:
        vpath = Path(__file__).resolve().parents[1] / "VERSION"
        if vpath.is_file():
            v = vpath.read_text(encoding="utf-8").strip()
            if v and not any(c.isspace() for c in v):
                return v
    except Exception:
        pass
    return _FALLBACK


__all__ = ["product_version"]
