"""
Takton Channel Slash Commands

参考 Hermes COMMAND_REGISTRY，为 Takton Channel 场景裁剪。
支持：/new /reset /compact /model /goal /status /help /stop
"""

from __future__ import annotations

import dataclasses
import logging
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable, Optional

logger = logging.getLogger(__name__)


# ─── 命令定义 ──────────────────────────────────────────────

class CommandCategory(str, Enum):
    SESSION = "会话"
    CONFIG = "配置"
    INFO = "信息"


@dataclass
class CommandDef:
    name: str
    description: str
    category: CommandCategory
    aliases: tuple[str, ...] = ()
    args_hint: str = ""


COMMAND_REGISTRY: list[CommandDef] = [
    # 会话
    CommandDef("new", "开启新会话", CommandCategory.SESSION, aliases=("reset",)),
    CommandDef("compact", "手动压缩上下文", CommandCategory.SESSION,
               aliases=("compress",), args_hint="[here [N] | focus <topic>]"),
    CommandDef("stop", "停止当前运行的 agent", CommandCategory.SESSION),
    CommandDef("goal", "设置持续目标（跨轮次）", CommandCategory.SESSION,
               args_hint="[text | show | pause | resume | clear]"),
    # 配置
    CommandDef("model", "切换模型", CommandCategory.CONFIG,
               args_hint="[model_name]"),
    CommandDef("tools", "管理工具：查看/启用/禁用", CommandCategory.CONFIG,
               args_hint="[list | enable <name> | disable <name>]"),
    CommandDef("toolset", "切换工具集（预设组合）", CommandCategory.CONFIG,
               args_hint="[list | <preset_name>]"),
    # 信息
    CommandDef("status", "查看会话/模型/上下文信息", CommandCategory.INFO),
    CommandDef("help", "显示可用命令", CommandCategory.INFO, aliases=("commands",)),
]

# 构建查找表
_lookup: dict[str, CommandDef] = {}
for _cmd in COMMAND_REGISTRY:
    _lookup[_cmd.name] = _cmd
    for _alias in _cmd.aliases:
        _lookup[_alias] = _cmd


def resolve_command(text: str) -> tuple[CommandDef | None, str]:
    """
    解析消息文本，如果是 /命令则返回 (CommandDef, args)，否则返回 (None, text)。
    支持 /command args 和 /command_args（Hermes 兼容）。
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None, text

    # /command args
    m = re.match(r"^/([a-zA-Z][a-zA-Z0-9_-]*)\s*(.*)", stripped, re.DOTALL)
    if not m:
        return None, text

    cmd_name = m.group(1).lower()
    args = m.group(2).strip()

    cmd = _lookup.get(cmd_name)
    if cmd:
        return cmd, args

    return None, text


def build_help_text() -> str:
    """生成 /help 命令的回复文本。"""
    lines = ["📋 Takton Channel 命令列表", ""]
    current_cat = None
    for cmd in COMMAND_REGISTRY:
        if cmd.category != current_cat:
            current_cat = cmd.category
            lines.append(f"── {current_cat.value} ──")
        alias_str = ""
        if cmd.aliases:
            alias_str = f" (/{', /'.join(cmd.aliases)})"
        args_str = f" {cmd.args_hint}" if cmd.args_hint else ""
        lines.append(f"  /{cmd.name}{args_str}{alias_str} — {cmd.description}")
    lines.append("")
    lines.append("💡 普通消息直接发送即可与 Takton 对话")
    return "\n".join(lines)


# ─── 工具集预设 ────────────────────────────────────────────

TOOLSET_PRESETS: dict[str, dict[str, Any]] = {
    "all": {
        "description": "全部工具",
        "tools": None,  # None = 全部启用
    },
    "safe": {
        "description": "安全模式（无命令行/文件写入）",
        "tools": ["browser", "file_read", "search", "http", "python", "glob", "grep", "sqlite_query"],
    },
    "coding": {
        "description": "编程模式（文件+命令+搜索）",
        "tools": ["command", "file_read", "file_write", "edit", "search", "glob", "grep", "python", "http"],
    },
    "search": {
        "description": "搜索模式（浏览器+搜索）",
        "tools": ["browser", "search", "http", "file_read"],
    },
    "minimal": {
        "description": "极简模式（仅搜索+读取）",
        "tools": ["search", "file_read"],
    },
    "none": {
        "description": "无工具（纯对话）",
        "tools": [],  # 空列表 = 全部禁用
    },
}


def build_toolset_list_text() -> str:
    """生成 /toolset list 的回复文本。"""
    lines = ["🔧 工具集预设", ""]
    for name, preset in TOOLSET_PRESETS.items():
        tools = preset["tools"]
        if tools is None:
            tool_str = "全部启用"
        elif len(tools) == 0:
            tool_str = "无工具"
        else:
            tool_str = ", ".join(tools)
        lines.append(f"  {name} — {preset['description']}")
        lines.append(f"    工具: {tool_str}")
    lines.append("")
    lines.append("💡 用法: /toolset <名称>")
    return "\n".join(lines)
