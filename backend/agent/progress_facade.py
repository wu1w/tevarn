"""Thin progress / thrash facade for tool_round and loop.

``progress_guard`` holds the pure policy; this module is the stable import
surface and optionally routes pure classifiers through the Rust kernel host
when available (with Python fallback — never dual-authority on control flow).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Re-export policy surface so call sites can depend on the facade only.
from backend.agent.progress_guard import (  # noqa: E402,F401
    DELIVER_TOOL_ALLOW,
    READ_ONLY_TOOLS,
    SCAN_TOOLS,
    WRITE_TOOLS,
    classify_grep_pattern,
    doom_loop_handoff,
    is_deliver_allowed_command,
    is_deliver_allowed_grep,
    is_diag_junk_path,
    is_probe_overwrite,
    is_review_only_task,
    resume_anchor_block,
    should_arm_deliver_mode,
)
from backend.agent.progress_guard import (  # noqa: E402
    classify_cargo_error as _py_classify_cargo_error,
)
from backend.agent.progress_guard import (
    is_cargo_compile_failure as _py_is_cargo_compile_failure,
)

_RUST_CLASSIFY_TRIED = False
_RUST_CLASSIFY_OK: bool | None = None


def _try_rust_classify(result: str) -> str | None:
    """Best-effort pure RPC; None → caller uses Python."""
    global _RUST_CLASSIFY_TRIED, _RUST_CLASSIFY_OK
    if _RUST_CLASSIFY_OK is False:
        return None
    try:
        from backend.kernel import get_kernel

        k = get_kernel()
        if k is None or not hasattr(k, "_call"):
            _RUST_CLASSIFY_OK = False
            return None
        out = k._call("cargo_classify", {"text": result or ""})
        if isinstance(out, dict):
            cls = str(out.get("class") or out.get("kind") or "").strip()
            if cls:
                _RUST_CLASSIFY_OK = True
                return cls
        if isinstance(out, str) and out.strip():
            _RUST_CLASSIFY_OK = True
            return out.strip()
    except Exception as e:
        if not _RUST_CLASSIFY_TRIED:
            logger.debug("cargo_classify rust path unavailable: %s", e)
        _RUST_CLASSIFY_TRIED = True
        # soft fail — keep trying next call? after first hard miss mark false
        # only if method missing; transient host blips stay soft
        msg = str(e).lower()
        if "unknown method" in msg or "not found" in msg or "method" in msg and "cargo" in msg:
            _RUST_CLASSIFY_OK = False
    return None


def classify_cargo_error(result: str) -> str:
    """Classify cargo failure: compile_source | path_env | toolchain | linker_env | unknown | ok.

    Prefers Rust host when ABI exposes ``cargo_classify``; falls back to Python
    with identical semantics so tests and degraded mode stay correct.
    """
    rust = _try_rust_classify(result)
    if rust:
        return rust
    return _py_classify_cargo_error(result)


def is_cargo_compile_failure(result: str) -> bool:
    """True only for source compile failures that should arm must_write."""
    try:
        from backend.core.config import settings as _st

        if not bool(getattr(_st, "agent_cargo_error_class_gate", True)):
            return _py_is_cargo_compile_failure(result)
    except Exception:
        pass
    return classify_cargo_error(result) == "compile_source"


def progress_snapshot(state: Any) -> dict[str, Any]:
    """Best-effort debug snapshot of progress_guard state on a run object."""
    out: dict[str, Any] = {}
    for key in (
        "deliver_mode",
        "must_write_before_cargo",
        "pure_read_streak",
        "file_read_count",
        "write_count",
        "cargo_fix_armed",
    ):
        if hasattr(state, key):
            try:
                out[key] = getattr(state, key)
            except Exception:
                pass
    return out
