"""
工具权限管理

统一处理所有工具（包括 skill/mcp/db）的权限校验：
- 路径白名单
- 危险操作确认
- 用户禁用/启用

工作区根解析（优先级从高到低）：
1. 构造参数 workspace_root
2. 环境变量 TAKTON_FILE_BROWSER_ROOT
3. settings.file_browser_root（相对路径相对「项目根」解析）
4. workspace 服务用户绑定 get_root(\"default\")
5. 自动探测项目根（含 backend/ 的目录）或 cwd
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from backend.tools.base import BaseTool, ToolRiskLevel

logger = logging.getLogger(__name__)


def detect_project_root(start: str | None = None) -> str:
    """向上查找含 backend/ 或 pyproject.toml 的项目根；找不到则 cwd。"""
    cur = Path(start or os.getcwd()).expanduser().resolve()
    if cur.is_file():
        cur = cur.parent
    for p in [cur, *cur.parents]:
        if (p / "backend").is_dir() and (
            (p / "pyproject.toml").is_file()
            or (p / "backend" / "main.py").is_file()
            or (p / "package.json").is_file()
        ):
            return str(p)
        # 单 backend 仓
        if (p / "main.py").is_file() and p.name == "backend":
            return str(p.parent)
    return str(cur)


def resolve_agent_workspace_root(explicit: str | None = None) -> str:
    """解析 Agent 文件/命令工具使用的工作区根（绝对路径）。"""
    if explicit:
        root = Path(explicit).expanduser()
        if not root.is_absolute():
            root = Path(detect_project_root()) / root
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        return str(root)

    env = (os.environ.get("TAKTON_FILE_BROWSER_ROOT") or "").strip()
    if env:
        root = Path(env).expanduser()
        if not root.is_absolute():
            root = Path(detect_project_root()) / root
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        return str(root)

    # settings.file_browser_root
    try:
        from backend.core.config import settings

        fb = (getattr(settings, "file_browser_root", None) or "").strip()
    except Exception:
        fb = ""

    if fb:
        root = Path(fb).expanduser()
        if not root.is_absolute():
            # 相对路径：相对项目根，而不是含糊 cwd
            root = Path(detect_project_root()) / root
        root = root.resolve()
        # "." → 项目根；"workspace" → 项目下 sandbox（自动创建）
        root.mkdir(parents=True, exist_ok=True)
        return str(root)

    # 用户绑定的专业模式根目录
    try:
        from backend.workspace.service import get_root

        bound = get_root("default")
        if bound is not None:
            return str(bound)
    except Exception:
        pass

    root = Path(detect_project_root())
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


class ToolPermissionManager:
    """工具权限管理器"""

    def __init__(self, workspace_root: str | None = None):
        self.workspace_root = resolve_agent_workspace_root(workspace_root)
        logger.debug("ToolPermissionManager workspace_root=%s", self.workspace_root)

    def _resolve_path(self, path: str) -> str:
        """解析路径为绝对路径（相对则拼 workspace_root）。"""
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
        try:
            target_p = Path(target).resolve()
        except OSError:
            target_p = Path(target)
        for allowed in paths:
            try:
                allowed_abs = Path(os.path.abspath(allowed)).resolve()
            except OSError:
                allowed_abs = Path(os.path.abspath(allowed))
            try:
                target_p.relative_to(allowed_abs)
                return True
            except ValueError:
                continue
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
            file_tools = {
                "file_read",
                "file_write",
                "edit",
                "glob",
                "grep",
                "sqlite_query",
                "apply_patch",
            }
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
    workspace = resolve_agent_workspace_root()
    uploads = os.environ.get("TAKTON_UPLOADS_DIR") or os.path.join(
        detect_project_root(), "uploads"
    )
    uploads = os.path.abspath(uploads)
    return [workspace, uploads]
