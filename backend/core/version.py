"""产品版本号：权威源 backend/VERSION。"""

from __future__ import annotations

from pathlib import Path

_FALLBACK = "0.6.0-alpha"


def product_version() -> str:
    try:
        vpath = Path(__file__).resolve().parents[1] / "VERSION"
        v = vpath.read_text(encoding="utf-8").strip()
        if v:
            return v
    except Exception:
        pass
    return _FALLBACK


__all__ = ["product_version"]
