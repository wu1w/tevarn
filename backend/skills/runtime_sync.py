"""Dynamic skill ↔ ToolRegistry 热同步。

对齐 MCP manage_mcp 热挂载：DB 写入后立即 register/unregister，
避免「创建成功但 agent 看不到工具」的 thrash。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def unregister_dynamic_skill(name: str) -> bool:
    """从 ToolRegistry 卸下动态 skill（若存在）。"""
    name = (name or "").strip()
    if not name:
        return False
    try:
        from backend.tools.registry import ToolRegistry

        if ToolRegistry.get(name) is None:
            return False
        ToolRegistry.unregister(name)
        return True
    except Exception as e:
        logger.debug("unregister_dynamic_skill %s: %s", name, e)
        return False


def register_dynamic_skill(skill_row: Any) -> bool:
    """把 DB skill 行热挂到 ToolRegistry。

    - builtin / 空 name：跳过
    - disabled：unregister 后返回 False
    - 已存在同名：先 unregister 再 register（更新 schema/handler）
    """
    if skill_row is None:
        return False
    name = str(getattr(skill_row, "name", "") or "").strip()
    if not name:
        return False
    if bool(getattr(skill_row, "is_builtin", False)):
        return False

    enabled = bool(getattr(skill_row, "enabled", True))
    if not enabled:
        unregister_dynamic_skill(name)
        return False

    try:
        from backend.skills.dynamic import DynamicSkill
        from backend.tools.adapters.dynamic_adapter import DynamicSkillAdapter
        from backend.tools.base import ToolSource
        from backend.tools.registry import ToolRegistry

        existing = ToolRegistry.get(name)
        if existing is not None and getattr(existing, "source", None) == ToolSource.BUILTIN:
            logger.warning("skip register dynamic skill %s: builtin tool exists", name)
            return False

        unregister_dynamic_skill(name)
        adapter = DynamicSkillAdapter(DynamicSkill.from_db(skill_row))
        ToolRegistry.register(adapter)
        return True
    except Exception as e:
        logger.warning("register_dynamic_skill %s failed: %s", name, e)
        return False


def sync_dynamic_skill_row(skill_row: Any, *, old_name: str | None = None) -> dict[str, Any]:
    """create/update/toggle 后统一同步。

    old_name: 改名时先卸旧名。
    """
    if old_name and old_name != getattr(skill_row, "name", None):
        unregister_dynamic_skill(str(old_name))
    ok = register_dynamic_skill(skill_row)
    return {
        "registered": ok,
        "name": getattr(skill_row, "name", None),
        "enabled": bool(getattr(skill_row, "enabled", False)) if skill_row else False,
    }
