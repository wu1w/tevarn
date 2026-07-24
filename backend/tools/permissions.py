"""
工具权限管理

统一处理所有工具（包括 skill/mcp/db）的权限校验：
- 路径白名单
- 危险操作确认
- 用户禁用/启用

工作区根解析（优先级从高到低）：
1. 构造参数 workspace_root
2. 本轮 run 上下文（session config: workspace_root / file_browser_root / cwd）
3. 环境变量 TAKTON_FILE_BROWSER_ROOT
4. settings.file_browser_root（相对路径相对「项目根」解析）
5. workspace 服务用户绑定 get_root("default")
6. 自动探测项目根（含 backend/ 的目录）或 cwd
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

from backend.tools.base import BaseTool, ToolRiskLevel

logger = logging.getLogger(__name__)

# 本轮 Agent run 覆盖（session 可配 workspace_root / cwd）
_run_workspace_root: ContextVar[str | None] = ContextVar("takton_run_workspace_root", default=None)
_run_extra_roots: ContextVar[tuple[str, ...] | None] = ContextVar(
    "takton_run_extra_roots", default=None
)


def get_run_workspace_root() -> str | None:
    return _run_workspace_root.get()


def get_run_extra_roots() -> list[str]:
    extra = _run_extra_roots.get()
    return list(extra) if extra else []


@contextmanager
def run_workspace_context(
    root: str | None = None,
    extra_roots: list[str] | None = None,
) -> Iterator[None]:
    """在 Agent.run 期间覆盖默认 workspace 与额外允许根。"""
    tokens: list = []
    if root:
        r = Path(root).expanduser()
        if not r.is_absolute():
            r = Path(detect_project_root()) / r
        r = r.resolve()
        r.mkdir(parents=True, exist_ok=True)
        tokens.append((_run_workspace_root, _run_workspace_root.set(str(r))))
        logger.info("run_workspace_context root=%s", r)
    if extra_roots is not None:
        cleaned: list[str] = []
        for e in extra_roots:
            if not e:
                continue
            ep = Path(str(e)).expanduser()
            if not ep.is_absolute():
                ep = Path(detect_project_root()) / ep
            try:
                ep = ep.resolve()
            except OSError:
                continue
            cleaned.append(str(ep))
        tokens.append((_run_extra_roots, _run_extra_roots.set(tuple(cleaned))))
        logger.info("run_workspace_context extra_roots=%s", cleaned)
    try:
        yield
    finally:
        for var, tok in reversed(tokens):
            var.reset(tok)


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

    run_root = _run_workspace_root.get()
    if run_root:
        return str(Path(run_root).resolve())

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



def bind_run_workspace_from_config(config: dict[str, Any] | None) -> Any:
    """从 session config 绑定本轮 workspace；返回 reset 回调。"""
    cfg = config if isinstance(config, dict) else {}
    root = (
        cfg.get("workspace_root")
        or cfg.get("file_browser_root")
        or cfg.get("cwd")
        or cfg.get("work_dir")
        or (os.environ.get("TAKTON_TASK_ROOT") or "").strip()
        or None
    )
    extra = cfg.get("allowed_roots") or cfg.get("extra_workspace_roots") or []
    if isinstance(extra, str):
        extra = [extra]
    # 若只设了 TASK_ROOT 且 env 已有 FILE_BROWSER_ROOT 不同，把 task 作为 root
    tokens: list = []
    if root:
        r = Path(str(root)).expanduser()
        if not r.is_absolute():
            r = Path(detect_project_root()) / r
        r = r.resolve()
        r.mkdir(parents=True, exist_ok=True)
        tokens.append((_run_workspace_root, _run_workspace_root.set(str(r))))
        logger.info("session workspace_root override=%s", r)
    if extra:
        cleaned: list[str] = []
        for e in extra:
            if not e:
                continue
            ep = Path(str(e)).expanduser()
            if not ep.is_absolute():
                ep = Path(detect_project_root()) / ep
            try:
                cleaned.append(str(ep.resolve()))
            except OSError:
                continue
        if cleaned:
            tokens.append((_run_extra_roots, _run_extra_roots.set(tuple(cleaned))))
            logger.info("session extra_roots=%s", cleaned)

    def _reset() -> None:
        for var, tok in reversed(tokens):
            try:
                var.reset(tok)
            except Exception:
                pass

    return _reset



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
        if allowed_paths is not None:
            paths = list(allowed_paths)
        else:
            paths = [self.workspace_root, *get_run_extra_roots()]
        # de-dupe
        seen: set[str] = set()
        uniq: list[str] = []
        for x in paths:
            k = str(x)
            if k not in seen:
                seen.add(k)
                uniq.append(k)
        paths = uniq
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
