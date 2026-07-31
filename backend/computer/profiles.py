"""Sandbox profiles: workspace | read-only | strict | off (Grok-inspired).

Maps product names to agent_computer_backend + network + permission hints.
OS-level isolation still depends on bwrap/seatbelt/job availability.

H-08：内核 isolation 角色（interactive / workforce / untrusted）与本表映射。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ProfileName = Literal["off", "workspace", "read_only", "strict"]
# Kernel isolation role names (Rust IsolationProfile)
IsolationRole = Literal["interactive", "workforce", "untrusted", "read_only"]


@dataclass(frozen=True)
class SandboxProfile:
    id: str
    label: str
    label_en: str
    # preferred backend when execution_mode wants sandbox
    prefer_backend: str  # auto | local | job | bwrap | ...
    network: bool
    # permission profile overlay when this sandbox is active
    force_working_mode: str | None = None  # e.g. readonly for read_only
    description: str = ""


PROFILES: dict[str, SandboxProfile] = {
    "off": SandboxProfile(
        id="off",
        label="关闭",
        label_en="Off",
        prefer_backend="local",
        network=True,
        description="无隔离，命令本机直跑。",
    ),
    "workspace": SandboxProfile(
        id="workspace",
        label="工作区",
        label_en="Workspace",
        prefer_backend="auto",
        network=True,
        description="可读写工作区；优先 OS 沙箱，保留网络。",
    ),
    "read_only": SandboxProfile(
        id="read_only",
        label="只读",
        label_en="Read-only",
        prefer_backend="auto",
        network=False,
        force_working_mode="readonly",
        description="探索/审代码：权限只读 + 沙箱断网。",
    ),
    "strict": SandboxProfile(
        id="strict",
        label="严格",
        label_en="Strict",
        prefer_backend="auto",
        network=False,
        force_working_mode="cautious",
        description="不信任代码：断网 + 谨慎权限 + 强制沙箱。",
    ),
}


def resolve_profile(raw: str | None = None) -> SandboxProfile:
    if raw is None:
        try:
            from backend.core.config import settings

            raw = getattr(settings, "agent_sandbox_profile", "workspace")
        except Exception:
            raw = "workspace"
    key = str(raw or "workspace").strip().lower().replace("-", "_")
    if key in ("readonly", "read-only"):
        key = "read_only"
    return PROFILES.get(key) or PROFILES["workspace"]


def apply_profile_to_backend_choice(backend_name: str, network: bool) -> tuple[str, bool]:
    """Adjust backend/network from active sandbox profile."""
    prof = resolve_profile()
    if prof.id == "off":
        return "local", True
    net = network if prof.network else False
    # strict/workspace keep auto unless user forced local
    if backend_name == "auto" and prof.prefer_backend != "auto":
        return prof.prefer_backend, net
    return backend_name, net


# H-08：isolation role → computer sandbox profile
_ROLE_TO_PROFILE: dict[str, str] = {
    "interactive": "workspace",
    "workforce": "workspace",
    "untrusted": "strict",
    "read_only": "read_only",
    "readonly": "read_only",
}


def profile_for_isolation_role(role: str | None) -> SandboxProfile:
    """Map kernel isolation role to computer SandboxProfile."""
    key = str(role or "interactive").strip().lower().replace("-", "_")
    return resolve_profile(_ROLE_TO_PROFILE.get(key, "workspace"))


def isolation_role_for_context(
    *,
    workforce: bool = False,
    untrusted: bool = False,
    readonly: bool = False,
) -> str:
    if untrusted:
        return "untrusted"
    if readonly:
        return "read_only"
    if workforce:
        return "workforce"
    return "interactive"


def degraded_local_flag(
    *,
    wanted_sandbox: bool,
    actual_backend: str | None,
) -> dict[str, Any]:
    """First-class degraded marker when sandbox was required but fell to local."""
    actual = str(actual_backend or "").strip().lower()
    degraded = bool(wanted_sandbox and actual in ("local", "none", "off", ""))
    return {
        "degraded": degraded,
        "execution_backend": actual or "unknown",
        "wanted_sandbox": wanted_sandbox,
        "reason": (
            "sandbox unavailable; running local (explicit degraded)"
            if degraded
            else ""
        ),
    }


def list_profiles() -> list[dict[str, Any]]:
    return [
        {
            "id": p.id,
            "label": p.label,
            "label_en": p.label_en,
            "network": p.network,
            "description": p.description,
            "force_working_mode": p.force_working_mode,
        }
        for p in PROFILES.values()
    ]
