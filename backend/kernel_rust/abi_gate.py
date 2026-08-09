"""Host ABI fail-closed gate — production refuses half-running hosts.

REQUIRED methods must exist after connect; missing → ConnectionError with
actionable rebuild instructions (not silent half-run).
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# Minimum ABI surface for 0.5 control plane (expand carefully, never shrink quietly).
REQUIRED_ABI_METHODS: frozenset[str] = frozenset(
    {
        "abi_version",
        "list_methods",
        "ping",
        "health",
        "create_process",
        "end_process",
        "mediate",
        "decide_tool",
        "filter_tools",
        "charge_tokens",
        "process_snapshot",
        "process_recovery_plan",
        "result_spill",
        "result_load",
        "run_gate_try",
        "run_gate_release",
        "iteration_set_budget",
        "iteration_consume",
        "marathon_record",
        "marathon_metrics",
        "events",
        "verify_event_chain",
    }
)


class AbiMismatchError(ConnectionError):
    """Host is up but ABI is incomplete / incompatible."""

    def __init__(self, missing: Iterable[str], host_methods: int = 0) -> None:
        miss = sorted({str(m) for m in missing if m})
        self.missing = miss
        msg = (
            f"kernel host ABI incomplete: missing {miss}. "
            f"host reports {host_methods} methods. "
            "Rebuild & stage: cargo build -p tevarn-kernel-host --release "
            "&& .\\scripts\\build-kernel-host.ps1 -Release "
            "(or node scripts/ensure-vendor-host.mjs). "
            "Do not continue with a half-running control plane."
        )
        super().__init__(msg)


def assert_required_abi(methods: list[str] | set[str] | None) -> list[str]:
    """Raise AbiMismatchError if required methods missing. Returns sorted missing (empty=ok)."""
    have = set(methods or [])
    missing = sorted(REQUIRED_ABI_METHODS - have)
    if missing:
        raise AbiMismatchError(missing, host_methods=len(have))
    return []


def check_required_abi(methods: list[str] | set[str] | None) -> dict[str, Any]:
    """Non-raising check for health/API."""
    have = set(methods or [])
    missing = sorted(REQUIRED_ABI_METHODS - have)
    return {
        "ok": not missing,
        "required": len(REQUIRED_ABI_METHODS),
        "have": len(have),
        "missing": missing,
        "coverage": round(len(REQUIRED_ABI_METHODS - set(missing)) / max(1, len(REQUIRED_ABI_METHODS)), 4),
    }
