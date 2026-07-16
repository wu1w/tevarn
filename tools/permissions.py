"""
工具权限管理

统一处理所有工具（包括 skill/mcp/db）的权限校验：
- 路径白名单
- 危险操作确认
- 用户禁用/启用
"""

from __future__ import annotations

import logging
import os
from typing import Any

from backend.tools.base import BaseTool, ToolRiskLevel

logger = logging.getLogger(__name__)


class ToolPermissionManager:
    """工具权限管理器"""

    def __init__(self, workspace_root: str | None = None):
        # 优先级：显式传入 > 环境变量 > workspace 服务绑定 > 默认 ./workspace
        if workspace_root:
            self.workspace_root = os.path.abspath(workspace_root)
        elif os.environ.get("TAKTON_FILE_BROWSER_ROOT"):
            self.workspace_root = os.path.abspath(
                os.environ.get("TAKTON_FILE_BROWSER_ROOT")
            )
        else:
            # 尝试从 workspace 服务获取当前用户绑定的根目录
            try:
                from backend.workspace.service import get_root

                # 单用户模式下用 default 用户
                root = get_root("default")
                if root:
                    self.workspace_root = str(root)
                else:
                    self.workspace_root = os.path.abspath("./workspace")
            except ImportError:
                self.workspace_root = os.path.abspath("./workspace")

    def _resolve_path(self, path: str) -> str:
        """解析路径为绝对路径"""
        if os.path.isabs(path):
            return os.path.abspath(path)
        return os.path.abspath(os.path.join(self.workspace_root, path))

    def is_path_allowed(self, path: str, allowed_paths: list[str] | None = None) -> bool:
        """
        检查路径是否允许访问。
        如果 allowed_paths 为 None，默认只允许 workspace_root。
        """
        paths = allowed_paths if allowed_paths else [self.workspace_root]
        target = self._resolve_path(path)
        for allowed in paths:
            allowed_abs = os.path.abspath(allowed)
            if target == allowed_abs or target.startswith(allowed_abs + os.sep):
                return True
        return False

    def check_tool_permission(
        self,
        tool: BaseTool,
        arguments: dict[str, Any],
    ) -> tuple[bool, str]:
        """
        统一权限检查入口。

        返回：(is_allowed, reason)
        """
        if not tool.enabled:
            return False, f"Tool '{tool.name}' is disabled"

        # 路径类权限检查
        if tool.allowed_paths is not None:
            path_keys = ["filepath", "path", "file", "directory", "dir", "base_path", "database"]
            for key in path_keys:
                if key in arguments and isinstance(arguments[key], str):
                    if not self.is_path_allowed(arguments[key], tool.allowed_paths):
                        return (
                            False,
                            f"Path '{arguments[key]}' is outside allowed directories: "
                            f"{tool.allowed_paths}",
                        )
        else:
            # 无显式 allowed_paths 时，对文件操作类工具做默认 workspace 边界检查
            file_tools = {"file_read", "file_write", "edit", "glob", "grep", "sqlite_query"}
            if tool.name in file_tools:
                path_keys = ["filepath", "path", "file", "directory", "dir", "base_path", "database"]
                for key in path_keys:
                    if key in arguments and isinstance(arguments[key], str):
                        if not self.is_path_allowed(arguments[key]):
                            return (
                                False,
                                f"Path '{arguments[key]}' is outside workspace root: "
                                f"{self.workspace_root}",
                            )

        return True, ""

    def needs_confirmation(self, tool: BaseTool, arguments: dict[str, Any]) -> bool:
        """判断是否需要用户确认"""
        if tool.requires_confirmation:
            return True

        if tool.risk_level == ToolRiskLevel.DANGEROUS:
            # 写操作类参数需要确认
            dangerous_params = ["content", "new_text", "body", "code", "command"]
            if any(p in arguments for p in dangerous_params):
                return True

        return False


def get_default_allowed_paths() -> list[str]:
    """获取默认允许路径"""
    workspace = os.environ.get("TAKTON_FILE_BROWSER_ROOT", os.path.abspath("./workspace"))
    uploads = os.environ.get("TAKTON_UPLOADS_DIR", os.path.abspath("./uploads"))
    return [workspace, uploads]
