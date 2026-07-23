"""L4 capability pack catalog helpers (sidecar, not default brain)."""
from __future__ import annotations

from backend.agent.tool_policy import TOOL_PACKS, list_pack_catalog, merge_tools_with_packs, resolve_enabled_tool_names

# 本阶段验收的主 sidecar packs
L4_PRIMARY_PACKS: tuple[str, ...] = ("devices", "desktop", "evolution")


def pack_tool_names(pack: str) -> list[str]:
    return list(TOOL_PACKS.get(pack, ()))


def coding_plus_packs(*packs: str) -> list[str] | None:
    """coding 默认面 + 指定 packs（模拟 use_tool_pack enable）。"""
    names, _ = resolve_enabled_tool_names(profile="coding", user_input="")
    return merge_tools_with_packs(names, packs)


def assert_pack_registered(registry_names: set[str], pack: str) -> list[str]:
    """返回缺失工具名（空=齐）。"""
    return [t for t in pack_tool_names(pack) if t not in registry_names]


__all__ = [
    "L4_PRIMARY_PACKS",
    "pack_tool_names",
    "coding_plus_packs",
    "assert_pack_registered",
    "list_pack_catalog",
]
