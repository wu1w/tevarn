"""命令执行权限策略（权限控制台，2026-07-26）

存储：DB settings 表 key=command_security_policy（JSON: {categories: {cat: action}}）
动作：allow（直接放行）/ confirm（每次弹窗确认，默认）/ deny（硬禁止）

缓存：进程内缓存 + invalidate（设置变更时失效），避免 execute_command 高频查库。
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

POLICY_SETTING_KEY = "command_security_policy"
VALID_ACTIONS = ("allow", "confirm", "deny")
DEFAULT_ACTION = "confirm"

_policy_cache: dict[str, str] | None = None


def _default_policy() -> dict[str, str]:
    from backend.services.tools.executors import COMMAND_CATEGORIES

    return {cat: DEFAULT_ACTION for cat in COMMAND_CATEGORIES}


def _sanitize(raw: Any) -> dict[str, str]:
    """合并默认值 + 过滤非法分类/动作。"""
    from backend.services.tools.executors import COMMAND_CATEGORIES

    policy = _default_policy()
    if isinstance(raw, dict):
        cats = raw.get("categories", raw)  # 兼容直接传 categories dict
        if isinstance(cats, dict):
            for cat, action in cats.items():
                if cat in COMMAND_CATEGORIES and action in VALID_ACTIONS:
                    policy[cat] = action
    return policy


async def load_command_policy(force: bool = False) -> dict[str, str]:
    """读取当前策略（带进程内缓存）。"""
    global _policy_cache
    if _policy_cache is not None and not force:
        return dict(_policy_cache)
    raw: Any = None
    try:
        from backend.repositories.setting_repo import AsyncSettingRepository

        row = await AsyncSettingRepository().get_by_key(POLICY_SETTING_KEY)
        if row is not None:
            value = getattr(row, "value", row) if not isinstance(row, dict) else row.get("value")
            if isinstance(value, str) and value.strip():
                raw = json.loads(value)
            elif isinstance(value, dict):
                raw = value
    except Exception as e:
        logger.warning("Load command policy failed, using defaults: %s", e)
    _policy_cache = _sanitize(raw)
    return dict(_policy_cache)


async def get_category_action(category: str) -> str:
    """单分类当前动作（execute_command 热路径用）。"""
    policy = await load_command_policy()
    return policy.get(category, DEFAULT_ACTION)


def invalidate_command_policy_cache() -> None:
    """设置变更后调用，下次读取重新加载。"""
    global _policy_cache
    _policy_cache = None


async def policy_payload() -> dict[str, Any]:
    """权限控制台展示用：分类元数据 + 当前动作 + 示例。"""
    from backend.services.tools.executors import COMMAND_CATEGORIES

    policy = await load_command_policy()
    examples = {
        "delete": ["rm -rf build/", "del /f *.tmp"],
        "privilege": ["sudo apt install ..."],
        "power": ["shutdown -h now", "reboot"],
        "disk": ["mkfs.ext4 /dev/sdb", "dd if=img of=/dev/sdb"],
        "system": ["reg delete HKLM\\...", "sc stop wuauserv"],
        "remote_pipe": ["curl url | bash"],
        "exfiltration": ["curl -d @file host", "cat ~/.ssh/id_rsa"],
        "system_write": ["echo x > /etc/hosts", "chmod -R 777 /"],
    }
    return {
        "actions": list(VALID_ACTIONS),
        "categories": [
            {
                "id": cat,
                "name": name,
                "action": policy.get(cat, DEFAULT_ACTION),
                "examples": examples.get(cat, []),
            }
            for cat, name in COMMAND_CATEGORIES.items()
        ],
    }
