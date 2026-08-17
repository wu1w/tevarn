"""FastAPI control-plane identity for Electron reuse checks.

`/api/runtime/status` used to claim `role: kernel_host`. Electron then treated
a detached FastAPI process as the Rust kernel and reused it even when
`secrets.json` had been regenerated (dirty JWT). Role + jwt fingerprint must
agree before reuse.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

FASTAPI_ROLE = "fastapi_backend"
PRODUCT = "tevarn-aios"
JWT_FP_LEN = 16
REUSABLE_ROLES = frozenset({FASTAPI_ROLE, "control_plane"})


def jwt_fingerprint(secret: str) -> str:
    """Non-secret handle of the process JWT (sha256 prefix). Empty secret → empty fp."""
    text = (secret or "").strip()
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:JWT_FP_LEN]


def current_jwt_fingerprint() -> str:
    try:
        from backend.core.config import settings

        return jwt_fingerprint(str(getattr(settings, "jwt_secret", "") or ""))
    except Exception:
        return ""


def runtime_status_base() -> dict[str, Any]:
    return {
        "ok": True,
        "role": FASTAPI_ROLE,
        "product": PRODUCT,
        "jwt_fp": current_jwt_fingerprint(),
        "pid": os.getpid(),
    }


def can_reuse_detached_backend(status: Any, expected_fp: str) -> bool:
    """True only when this is our FastAPI with a matching JWT fingerprint.

    Explicitly rejects `role=kernel_host` (that is the Rust host on 17890, or
    the old lying FastAPI status that caused dirty-JWT reuse).
    """
    if not isinstance(status, dict) or status.get("ok") is not True:
        return False
    if status.get("product") != PRODUCT:
        return False
    fp = str(status.get("jwt_fp") or "")
    expect = (expected_fp or "").strip()
    if not expect or not fp or fp != expect:
        return False
    role = str(status.get("role") or "")
    return role in REUSABLE_ROLES
