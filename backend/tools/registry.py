"""
统一工具注册表

负责所有来源工具的注册、发现、schema 生成和执行。
来源包括：
- builtin（内置工具）
- skill（原有 Skill 适配）
- dynamic（用户自定义 Skill）
- db（数据库 Tool 适配）
- mcp（MCP 工具适配）
"""

from __future__ import annotations

import logging
from typing import Any

from backend.tools.base import BaseTool, ToolSource

logger = logging.getLogger(__name__)


class ToolRegistry:
    """统一工具注册表（单例）"""

    _tools: dict[str, BaseTool] = {}
    _instance_lock = None  # 用于测试隔离

    @classmethod
    def _ensure_clean(cls) -> None:
        """确保每个测试用例有干净的注册表（通过 pytest fixture 调用）"""
        pass

    @classmethod
    def reset(cls) -> None:
        """完全重置注册表（测试隔离用）"""
        cls._tools = {}

    # 工具来源优先级：数字越小优先级越高
    # 高优先级不会被低优先级覆盖
    _SOURCE_PRIORITY: dict[str, int] = {
        "builtin": 0,
        "skill": 1,
        "dynamic": 2,
        "db": 3,
        "mcp": 4,
    }

    @classmethod
    def register(cls, tool: BaseTool) -> None:
        """注册工具；同名时按来源优先级保留高优先级"""
        if not tool.name:
            logger.warning("Tool without name skipped")
            return

        existing = cls._tools.get(tool.name)
        if existing is not None:
            existing_priority = cls._SOURCE_PRIORITY.get(existing.source.value, 99)
            new_priority = cls._SOURCE_PRIORITY.get(tool.source.value, 99)
            if new_priority > existing_priority:
                logger.debug(
                    f"Skipped registering {tool.name} from {tool.source.value} "
                    f"because higher priority {existing.source.value} already exists"
                )
                return

        cls._tools[tool.name] = tool
        logger.debug(f"Registered tool: {tool.name} (source={tool.source.value})")

    @classmethod
    def unregister(cls, name: str) -> None:
        cls._tools.pop(name, None)

    @classmethod
    def clear(cls) -> None:
        cls._tools.clear()

    @classmethod
    def get(cls, name: str) -> BaseTool | None:
        return cls._tools.get(name)

    @classmethod
    def get_all(cls, source: ToolSource | None = None) -> list[BaseTool]:
        """获取所有已注册工具，可选按来源过滤"""
        tools = list(cls._tools.values())
        if source is not None:
            tools = [t for t in tools if t.source == source]
        return tools

    @classmethod
    def get_by_source(cls, source: str) -> list[BaseTool]:
        return [t for t in cls._tools.values() if t.source.value == source]

    @classmethod
    def get_tools_schema(cls, names: list[str] | None = None) -> list[dict[str, Any]]:
        """
        获取 LLM 可用的工具 schema 列表。

        names: 只返回这些名称的工具；None 表示全部启用的工具。
        """
        tools = []
        for name, tool in cls._tools.items():
            if names is not None and name not in names:
                continue
            if not tool.enabled:
                continue
            tools.append(tool.to_json_schema())
        return tools

    @classmethod
    async def execute(cls, name: str, arguments: dict[str, Any]) -> Any:
        """执行指定工具（含 before/after hooks）。"""
        tool = cls._tools.get(name)
        if tool is None:
            return f"[Error] Tool '{name}' not found"

        if not tool.enabled:
            return f"[Error] Tool '{name}' is disabled"

        args = dict(arguments or {})

        # L3 hooks —— 权限体系就挂在这里，因此必须 fail-closed。
        # 此前这个 try 捕获后没有 return，继续往下执行工具：任何 import 错误
        # 都能让整套权限规则被静默跳过，而日志只有一行 warning。
        try:
            from backend.agent.tool_hooks import (
                ensure_builtin_hooks_registered,
                run_before_tool_call,
            )

            ensure_builtin_hooks_registered()
            before = await run_before_tool_call(name, args)
        except Exception as e:
            logger.error(
                "before_tool_call machinery failed for tool=%s; refusing the call "
                "(fail-closed)",
                name,
                exc_info=True,
            )
            return (
                f"[Security Blocked] 权限检查未能执行（{type(e).__name__}: {e}），"
                f"已拒绝调用 '{name}'。这是 Takton 内部错误，请查看后端日志。"
            )

        if before.block:
            return f"[Hook Blocked] {before.reason or 'blocked'}"
        if before.arguments is not None:
            args = dict(before.arguments)

        # 权限检查（hook 可改参后）
        from backend.tools.permissions import ToolPermissionManager

        manager = ToolPermissionManager()
        allowed, reason = manager.check_tool_permission(tool, args)
        if not allowed:
            return f"[Security Blocked] {reason}"

        # 内部 meta：只剥离「hook 自用、执行器用不上」的键。
        #
        # 注意 _session_id 必须穿透到执行器 —— executors.execute_command /
        # execute_python 的危险操作确认走 confirm_manager.request_confirmation(
        # ws_manager, session_id)，session_id 为 None 时 ConnectionManager.broadcast
        # 查不到连接会静默 return，确认请求永远推不到前端，协程干等 30s 超时后
        # 返回「用户已拒绝」——用户根本没被问过。（此前 _session_id 在这里被剥掉，
        # 使 COMMAND_CATEGORIES 八类默认的 confirm 动作全部失效。）
        # _ws_manager 一直是穿透的，两者必须同进同出。
        _DROP_META = {"_chat_mode", "_history_point", "_checkpoint_path"}
        exec_args = {
            k: v
            for k, v in args.items()
            if k not in _DROP_META and not str(k).startswith("_checkpoint")
        }

        result = await tool.execute(**exec_args)

        try:
            import json as _json

            from backend.agent.tool_hooks import run_after_tool_call
            from backend.agent.tool_result_contract import normalize_tool_result

            if isinstance(result, dict):
                # drop huge unused image field if empty path present
                data = result.get("data")
                if isinstance(data, dict) and data.get("path") and not data.get("image"):
                    pass
                text = _json.dumps(result, ensure_ascii=False, default=str)
            else:
                text = normalize_tool_result(result, tool_name=name)
            text = await run_after_tool_call(name, exec_args, text)
            return text
        except Exception as e:
            logger.warning("after_tool_call failed: %s", e)
            return result
